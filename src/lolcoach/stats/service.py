"""Prepara las estadísticas al conectar con el cliente y las pasa a la fuente de builds.

* Usa el parche de tu cliente. Si para ese parche aún hay pocas partidas (recién
  salido), usa el anterior si tiene más datos.
* La agregación se guarda en caché y solo se recalcula si hay partidas nuevas.
* Sin clave de Riot (o con pocos datos propios) se usan las estadísticas compartidas
  (``pack.py``): de las dos, la que tenga datos suficientes del parche más reciente.
* Quien lo tiene configurado publica sus estadísticas para los demás (``publish.py``).
* Todo el trabajo pesado se hace en segundo plano, sin bloquear la app.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient
from ..lcu.connector import LcuConnector
from ..models import ClientInfo
from .aggregate import (
    Stats,
    aggregate,
    is_fresh,
    lane_matchups,
    load_stats,
    save_stats,
    with_matchups,
)
from .extract import patch_key
from .pack import SharedStats
from .publish import PackPublisher
from .source import StatsBuildSource
from .store import DATABASE, MatchStore, is_corrupt, set_aside

log = logging.getLogger(__name__)
ui = logging.getLogger("lolcoach.ui")

ENOUGH_MATCHES = 100  # por debajo, se prefiere el parche anterior si tiene más datos


def stats_file(stats_dir: Path, patch: str) -> Path:
    return stats_dir / f"builds-{patch}.json"


def prepare_stats(stats_dir: Path, patch: str, gamedata: GameData) -> Stats | None:
    """Elige el parche, agrega (o lee la caché) y devuelve las estadísticas. Bloqueante.

    Con la base dañada no falla: la aparta (sin borrarla) y devuelve None (reglas).
    """
    database = stats_dir / DATABASE
    if not database.exists():
        return None
    try:
        return _prepare(database, stats_dir, patch, gamedata)
    except sqlite3.DatabaseError as exc:
        if not is_corrupt(exc):
            raise
        log.warning("La base de estadísticas se ha dañado: %s", exc)
        try:
            set_aside(database)
        except (OSError, sqlite3.DatabaseError) as err:  # abierta en otro programa: luego
            log.warning("No se ha podido apartar la base dañada todavía: %s", err)
        return None


def _prepare(database: Path, stats_dir: Path, patch: str, gamedata: GameData) -> Stats | None:
    store = MatchStore(database)  # si está dañada al abrirla, la aparta y empieza otra
    try:
        counts = dict(store.patches())
        chosen = patch
        if counts.get(patch, 0) < ENOUGH_MATCHES:
            older = [p for p in counts if patch_key(p) < patch_key(patch)]
            previous = max(older, key=patch_key, default=None)
            if previous and counts[previous] > counts.get(patch, 0):
                chosen = previous
        matches = counts.get(chosen, 0)
        if not matches:
            return None
        path = stats_file(stats_dir, chosen)
        loadouts = store.loadout_count(chosen)
        if is_fresh(path, matches, loadouts) and (cached := load_stats(path)) is not None:
            return cached
        stats = aggregate(store.builds(chosen), gamedata.item)
        stats = with_matchups(stats, lane_matchups(store.games(chosen)))
        save_stats(path, chosen, matches, stats, loadouts)
        return chosen, matches, stats
    finally:
        store.close()


def choose(client_patch: str, *options: Stats | None) -> Stats | None:
    """La mejor fuente: con datos suficientes, del parche más reciente (sin pasarse del
    cliente) y con más partidas. Ante un empate gana la primera (las propias)."""

    def score(stats: Stats) -> tuple[bool, bool, tuple[int, ...], int]:
        patch, matches, _ = stats
        key = patch_key(patch)
        return (key <= patch_key(client_patch), matches >= ENOUGH_MATCHES, key, matches)

    present = [o for o in options if o is not None]
    return max(present, key=score, default=None)


class StatsService:
    def __init__(
        self,
        connector: LcuConnector,
        gamedata: GameData,
        source: StatsBuildSource,
        stats_dir: Path,
        *,
        shared: SharedStats | None = None,
        publisher: PackPublisher | None = None,
    ) -> None:
        self._gamedata = gamedata
        self._source = source
        self._stats_dir = stats_dir
        self._shared = shared
        self._publisher = publisher
        self._applied: tuple[str, int, bool] | None = None
        self._publishing: asyncio.Task[bool] | None = None
        connector.on_connect(self._on_connect)

    async def _on_connect(self, _client: LcuClient, info: ClientInfo) -> None:
        await self.refresh(info.patch)

    async def refresh(self, patch: str) -> None:
        own = await self._own(patch)
        if own is not None:
            self._publish(own)
        shared = await self._shared.get() if self._shared is not None else None
        result = choose(patch, own, shared)
        if result is None:
            log.debug("Sin estadísticas: se usan las builds por reglas")
            return
        chosen, matches, stats = result
        is_shared = result is not own
        if self._applied == (chosen, matches, is_shared):
            return  # nada nuevo: que las builds guardadas no se recalculen sin motivo
        self._applied = (chosen, matches, is_shared)
        self._source.update(chosen, matches, stats)
        ui.info(
            "Builds con estadísticas reales%s: %d campeones/posiciones con datos (%d partidas, "
            "parche %s)",
            " compartidas" if is_shared else "",
            self._source.covered,
            matches,
            chosen,
        )

    async def _own(self, patch: str) -> Stats | None:
        try:
            return await asyncio.to_thread(prepare_stats, self._stats_dir, patch, self._gamedata)
        except (sqlite3.DatabaseError, OSError) as exc:  # p. ej., ocupada: se sigue igual
            log.warning("No se han podido leer las estadísticas: %s", exc)
            return None

    def _publish(self, stats: Stats) -> None:
        if self._publisher is None or (self._publishing and not self._publishing.done()):
            return
        self._publishing = asyncio.create_task(
            asyncio.to_thread(self._publisher.maybe_publish, stats)
        )
        self._publishing.add_done_callback(_log_failure)


def _log_failure(task: asyncio.Task[bool]) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        log.error("La publicación de estadísticas ha fallado", exc_info=exc)
