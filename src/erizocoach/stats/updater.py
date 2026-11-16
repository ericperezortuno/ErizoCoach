"""Descarga automática de estadísticas mientras Erizo Coach está abierto.

* Solo si hay clave de Riot guardada (y ``stats_auto_download`` no está desactivado).
* Se pausa en la selección de campeones y durante la partida, para no gastar red
  mientras juegas; sigue sola al volver al menú.
* Descarga de varias regiones a la vez (``stats_regions``: EUW, Corea y Norteamérica),
  cada una con su propio límite de peticiones de Riot.
* Descarga por tandas de unos minutos; tras cada tanda recalcula las estadísticas,
  así que las siguientes partidas ya usan los datos nuevos sin reiniciar la app.
* Se detiene cuando el parche tiene ``stats_target_matches`` partidas (todas con
  runas). Con un parche nuevo vuelve a empezar y borra los parches viejos.
* Si la clave caduca, se detiene y lo avisa en la consola y en la ventana.
* Si ya hay una descarga en marcha desde la consola (``stats descargar``), espera.
* Si la base se daña, se aparta sin borrarla, se empieza otra y se avisa en la ventana.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..bus import EventBus
from ..config import Settings
from ..events import GameflowPhaseChanged, StatsStatus
from ..lcu.client import LcuClient
from ..lcu.connector import LcuConnector
from ..logs import REDACTOR
from ..models import ClientInfo, GameflowPhase
from ..single_instance import SingleInstanceLock
from . import keystore
from .crawler import crawl_regions
from .extract import patch_key
from .riot_api import RiotApiClient, RiotApiError, RiotApiKeyError
from .store import DATABASE, MatchStore, is_corrupt, set_aside

log = logging.getLogger(__name__)
ui = logging.getLogger("erizocoach.ui")

PAUSE_PHASES = frozenset(
    {
        GameflowPhase.CHAMP_SELECT,
        GameflowPhase.GAME_START,
        GameflowPhase.IN_PROGRESS,
        GameflowPhase.RECONNECT,
    }
)
BATCH_SECONDS = 300.0  # cada tanda: unos 5 minutos de descarga y se recalcula
RETRY_SECONDS = 600.0  # sin conexión o con otra descarga en marcha: se reintenta luego
LOCK_FILE = "descarga.lock"

ApiFactory = Callable[[str, str], RiotApiClient]


class StatsRefresher(Protocol):
    async def refresh(self, patch: str) -> None: ...


def download_lock(stats_dir: Path) -> SingleInstanceLock:
    """Una sola descarga a la vez (app o consola): comparten la clave y sus límites."""
    return SingleInstanceLock(stats_dir / LOCK_FILE)


class StatsUpdater:
    def __init__(
        self,
        bus: EventBus,
        connector: LcuConnector | None,
        stats: StatsRefresher,
        settings: Settings,
        *,
        key_loader: Callable[[], str | None] = keystore.load_key,
        api_factory: ApiFactory | None = None,
        batch_seconds: float = BATCH_SECONDS,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self._bus = bus
        self._stats = stats
        self._settings = settings
        self._key_loader = key_loader
        self._api_factory = api_factory or (lambda key, region: RiotApiClient(key, region))
        self._batch_seconds = batch_seconds
        self._retry_seconds = retry_seconds
        self._patch = ""
        self._playing = False
        self._finished_patch = ""  # parche ya completo (o sin clave): no se repite
        self._announced = ""  # parche cuyo inicio de descarga ya se ha mostrado
        self._warning = ""  # aviso que acompaña a cada estado (base dañada apartada)
        self.task: asyncio.Task[None] | None = None

        if connector is not None:
            connector.on_connect(self._on_connect)
        bus.subscribe(GameflowPhaseChanged, self._on_phase)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def _on_connect(self, _client: LcuClient | None, info: ClientInfo) -> None:
        if info.patch != self._patch:
            self._finished_patch = ""  # parche nuevo (o cliente reabierto): se vuelve a mirar
        self._patch = info.patch
        self._start()

    async def _on_phase(self, event: GameflowPhaseChanged) -> None:
        self._playing = event.current in PAUSE_PHASES
        if self._playing:
            await self.stop()
        else:
            self._start()

    def _start(self) -> None:
        if (
            not self._settings.stats_auto_download
            or self._playing
            or not self._patch
            or self._finished_patch == self._patch
            or self.running
        ):
            return
        self.task = asyncio.create_task(self._run(self._patch))
        self.task.add_done_callback(_log_failure)

    async def stop(self) -> None:
        """Pausa (se reanuda donde se quedó: lo descargado ya está guardado)."""
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self, patch: str) -> None:
        key = await asyncio.to_thread(self._key_loader)
        if not key:
            self._finished_patch = patch
            await self._status(
                "Estadísticas sin descarga automática: no hay clave de Riot guardada "
                "(erizocoach-consola stats clave)."
            )
            return
        REDACTOR.add_secret(key)
        lock = download_lock(self._settings.stats_dir)
        told_waiting = False
        # Un cliente por región para todas las tandas: así recuerda las peticiones recientes y
        # nunca supera los límites de Riot al empezar una tanda nueva.
        async with contextlib.AsyncExitStack() as stack:
            apis = [
                await stack.enter_async_context(self._api_factory(key, region))
                for region in self._settings.stats_regions
            ]
            while True:
                if not lock.acquire():
                    if not told_waiting:
                        await self._status(
                            "Esperando: hay otra descarga de estadísticas en marcha."
                        )
                        told_waiting = True
                    await asyncio.sleep(self._retry_seconds)
                    continue
                try:
                    done = await self._batch(apis, patch)
                finally:
                    lock.release()
                if done:
                    self._finished_patch = patch
                    return

    async def _batch(self, apis: list[RiotApiClient], patch: str) -> bool:
        """Una tanda de descarga. Devuelve True si ya no hay que seguir con este parche.

        Un problema con la base no detiene la descarga: si está dañada se aparta (sin
        borrarla) y la siguiente tanda empieza con una nueva; si está ocupada, se reintenta.
        """
        try:
            return await self._download(apis, patch)
        except sqlite3.DatabaseError as exc:
            if not is_corrupt(exc):
                log.warning("Base de estadísticas ocupada o no disponible: %s", exc)
                await asyncio.sleep(self._retry_seconds)
                return False
            log.warning("La base de estadísticas se ha dañado durante la descarga: %s", exc)
            try:
                await asyncio.to_thread(set_aside, self._database)
            except (OSError, sqlite3.DatabaseError) as err:
                await self._cannot_open(err)
            return False  # la siguiente tanda abre la base nueva y lo avisa en la ventana
        except OSError as exc:  # p. ej., la base dañada sigue abierta en otro programa
            await self._cannot_open(exc)
            return False

    async def _cannot_open(self, exc: Exception) -> None:
        await self._status(
            f"No se puede usar la base de estadísticas ({exc}): se reintentará en unos "
            "minutos. Si sigue igual, cierra la consola de Erizo Coach si la tienes abierta.",
            problem=True,
        )
        await asyncio.sleep(self._retry_seconds)

    @property
    def _database(self) -> Path:
        return self._settings.stats_dir / DATABASE

    async def _download(self, apis: list[RiotApiClient], patch: str) -> bool:
        target = self._settings.stats_target_matches
        # Abrir (con su comprobación) y podar tardan: fuera del bucle, que la ventana y los
        # eventos del cliente no esperen. Si está dañada, la aparta y empieza otra.
        store = await asyncio.to_thread(MatchStore, self._database)
        try:
            if (moved := store.take_quarantine_notice()) is not None:
                self._warning = (
                    f"La base anterior estaba dañada: se ha apartado (sin borrarla) como "
                    f"«{moved}» y las partidas se vuelven a descargar."
                )
                self._announced = ""  # que el aviso se vea ya, sin esperar a la tanda
            await asyncio.to_thread(_prune, store, patch)
            before = store.match_count(patch)
            if before >= target and not store.matches_without_loadout(patch, limit=1):
                self._warning = ""
                await self._status(_summary(store, patch, "al día"))
                return True
            if self._announced != patch:  # que se vea desde el principio, no tras la tanda
                self._announced = patch
                await self._status(_summary(store, patch, "descargando"))
            try:
                result = await crawl_regions(
                    apis, store, patch=patch, max_matches=max(target - before, 0),
                    max_seconds=self._batch_seconds,
                )  # fmt: skip
            except RiotApiKeyError as exc:
                await self._status(
                    f"La clave de Riot ha caducado o no es válida ({exc.riot_message or exc}): "
                    "las estadísticas no se actualizan. Renuévala con «erizocoach-consola stats "
                    "clave».",
                    problem=True,
                )
                return True
            except (RiotApiError, OSError) as exc:
                log.warning("Descarga de estadísticas interrumpida: %s", exc)
                await asyncio.sleep(self._retry_seconds)
                return False
            for region, error in result.errors.items():
                log.warning("Descarga de %s interrumpida: %s", region, error)
            finished = (result.out_of_players and not result.errors) or (
                store.match_count(patch) >= target
                and not store.matches_without_loadout(patch, limit=1)
            )
            summary = _summary(store, patch, "al día" if finished else "descargando")
        finally:
            store.close()
        await self._stats.refresh(patch)  # las siguientes partidas ya usan los datos nuevos
        if finished:
            self._warning = ""  # base rehecha: ya no hace falta el aviso
        await self._status(summary)
        if not finished and not result.advanced:
            await asyncio.sleep(self._retry_seconds)  # tanda sin avances: no insistir en bucle
        return finished

    async def _status(self, text: str, *, problem: bool = False) -> None:
        if self._warning:  # base dañada apartada: se avisa hasta que vuelva a estar al día
            text, problem = f"{text} {self._warning}", True
        (ui.warning if problem else ui.info)("%s", text)
        await self._bus.publish(StatsStatus(text, problem))


def _log_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        log.error("La descarga automática de estadísticas ha fallado", exc_info=exc)


def _summary(store: MatchStore, patch: str, state: str) -> str:
    total, with_runes = store.match_count(patch), store.loadout_count(patch)
    regions = store.matches_by_region(patch)
    split = " · ".join(f"{n} {region}" for region, n in regions.items())
    return (
        f"Estadísticas {state}: {total} partidas de alto elo del parche {patch} "
        f"({with_runes} con runas y hechizos{'; ' + split if len(regions) > 1 else ''})."
    )


def _prune(store: MatchStore, patch: str) -> None:
    """Conserva el parche actual y el anterior (se usa mientras el nuevo tiene pocos datos)."""
    older = [p for p, _ in store.patches() if patch_key(p) < patch_key(patch)]
    previous = max(older, key=patch_key, default=patch)
    removed = store.prune(keep_from=previous)
    if removed:
        log.info("Borradas %d partidas de parches antiguos", removed)
