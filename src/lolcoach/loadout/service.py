"""Orquesta la fase 2: cuándo calcular y aplicar las runas y los hechizos.

* Mientras se bloquean campeones: se descargan por adelantado los datos
  necesarios (perfiles, recomendaciones), en segundo plano.
* Mientras preseleccionas o miras un campeón: se calculan sus runas y hechizos para
  enseñarlos ya (``LoadoutPreview``), sin aplicar nada.
* Al hacerse el último pick (``PicksFinalized``): se calcula y se aplica.
* Si después cambia tu campeón (intercambio o banquillo de ARAM): se vuelve a aplicar.
* Si cambias runas o hechizos a mano, no se vuelve a tocar nada en esa selección.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from ..bus import EventBus
from ..champselect.models import ChampSelectSnapshot
from ..config import Settings
from ..events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectStarted,
    ClientDisconnected,
    LoadoutApplied,
    LoadoutPreview,
    LoadoutReady,
    MyChampionChanged,
    MyPickPreviewChanged,
    PicksFinalized,
)
from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient, LcuError
from ..lcu.connector import LcuConnector
from ..models import QueueInfo
from .applier import LoadoutApplier, NoRunePageSlot, page_title
from .models import Loadout, LoadoutStats
from .positions import PositionHint
from .recommender import LoadoutRecommender

log = logging.getLogger(__name__)

# El cliente a veces activa su página temporal justo al bloquear: lo comprobamos poco después.
PAGE_CHECK_DELAY = 1.5
# Al pasar de un campeón a otro rápido, solo se calcula el último en el que te paras.
PREVIEW_DELAY = 0.3


class LoadoutService:
    def __init__(
        self,
        bus: EventBus,
        connector: LcuConnector,
        gamedata: GameData,
        settings: Settings,
        *,
        page_check_delay: float = PAGE_CHECK_DELAY,
        preview_delay: float = PREVIEW_DELAY,
        position_hint: PositionHint | None = None,
        stats: LoadoutStats | None = None,
    ) -> None:
        self._bus = bus
        self._connector = connector
        self._gamedata = gamedata
        self._settings = settings
        self._page_check_delay = page_check_delay
        self._preview_delay = preview_delay
        self._preview_task: asyncio.Task[Any] | None = None
        self._recommender = LoadoutRecommender(gamedata, position_hint, stats)
        self._applier = LoadoutApplier()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._generation = 0
        self._queue: QueueInfo | None = None
        self._finalized = False
        self._applied_champion = 0

        bus.subscribe(ChampSelectStarted, self._on_started)
        bus.subscribe(ChampionLocked, self._on_locked)
        bus.subscribe(MyChampionChanged, self._on_my_champion)
        bus.subscribe(MyPickPreviewChanged, self._on_preview)
        bus.subscribe(PicksFinalized, self._on_finalized)
        bus.subscribe(ChampSelectEnded, self._on_end)
        bus.subscribe(ClientDisconnected, self._on_end)

    # --- Eventos --------------------------------------------------------------------

    def _on_started(self, event: ChampSelectStarted) -> None:
        self._reset()
        self._queue = event.queue

    def _on_end(self, _event: object) -> None:
        self._reset()
        # Las precargas y comprobaciones pendientes ya no sirven: se cancelan.
        for task in list(self._tasks):
            task.cancel()

    def _on_locked(self, event: ChampionLocked) -> None:
        client = self._connector.client
        if client is not None:
            self._spawn(self._gamedata.profile(client, event.slot.champion_id), "perfil")

    async def _on_my_champion(self, event: MyChampionChanged) -> None:
        if self._finalized:
            await self._run(event.snapshot)  # intercambio o banquillo tras el último pick
            return
        client = self._connector.client
        if client is not None:
            self._spawn(self._recommender.prefetch(client, event.snapshot, self._queue), "precarga")

    def _on_preview(self, event: MyPickPreviewChanged) -> None:
        if self._finalized:
            return
        if self._preview_task is not None:
            self._preview_task.cancel()  # ya miras otro: el anterior no interesa
        self._preview_task = self._spawn(self._preview(self._generation, event), "previsión")

    async def _preview(self, generation: int, event: MyPickPreviewChanged) -> None:
        await asyncio.sleep(self._preview_delay)
        client = self._connector.client
        if client is None:
            return
        loadout = None
        if event.champion_id:
            snapshot = event.snapshot.with_my_champion(event.champion_id)
            loadout = await self._recommender.recommend(client, snapshot, self._queue)
        if generation == self._generation and not self._finalized:
            await self._bus.publish(LoadoutPreview(event.champion_id, loadout))

    async def _on_finalized(self, event: PicksFinalized) -> None:
        self._finalized = True
        self._queue = event.queue or self._queue
        await self._run(event.snapshot)

    # --- Cálculo y aplicación ---------------------------------------------------------

    async def _run(self, snapshot: ChampSelectSnapshot) -> None:
        client = self._connector.client
        me = snapshot.me
        if client is None or me is None or not me.has_champion:
            return
        if me.champion_id == self._applied_champion:
            return  # ya aplicado para este campeón: respetamos tus cambios manuales
        loadout = await self._recommender.recommend(client, snapshot, self._queue)
        if loadout is None:
            return
        self._applied_champion = me.champion_id
        await self._bus.publish(LoadoutReady(loadout))
        await self._bus.publish(await self._apply(client, loadout, snapshot))

    async def _apply(
        self, client: LcuClient, loadout: Loadout, snapshot: ChampSelectSnapshot
    ) -> LoadoutApplied:
        problems: list[str] = []
        page_name: str | None = None
        runes_applied = spells_applied = False

        if loadout.runes is not None and self._settings.auto_runes:
            title = page_title(self._gamedata.champion_name(loadout.champion_id))
            try:
                page_id = await self._applier.apply_runes(client, loadout.runes, title)
            except NoRunePageSlot:
                problems.append(
                    "No hay hueco para la página «LoL Coach»: renombra una de tus páginas a "
                    "«LoL Coach» (o borra una) y se usará esa. Pon las runas a mano esta vez."
                )
            except LcuError as exc:
                problems.append(f"No se pudieron poner las runas: {exc}")
            else:
                runes_applied, page_name = True, title
                self._spawn(self._check_page(self._generation, page_id), "comprobar página")

        me = snapshot.me
        if loadout.spells is not None and self._settings.auto_spells and me is not None:
            if (me.spell1_id, me.spell2_id) == loadout.spells:
                spells_applied = True  # ya los tenías
            else:
                try:
                    await self._applier.apply_spells(client, loadout.spells)
                    spells_applied = True
                except LcuError as exc:
                    problems.append(f"No se pudieron poner los hechizos: {exc}")

        return LoadoutApplied(loadout, page_name, runes_applied, spells_applied, tuple(problems))

    async def _check_page(self, generation: int, page_id: int) -> None:
        await asyncio.sleep(self._page_check_delay)
        client = self._connector.client
        if client is None or generation != self._generation:
            return
        if await self._applier.ensure_current_page(client, page_id):
            log.info("El cliente había activado su página temporal; se ha vuelto a poner la tuya.")

    # --- Utilidades ---------------------------------------------------------------------

    def _reset(self) -> None:
        self._generation += 1
        self._preview_task = None
        self._queue = None
        self._finalized = False
        self._applied_champion = 0

    def _spawn(self, coroutine: Coroutine[Any, Any, Any], what: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def done(finished: asyncio.Task[Any]) -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                log.debug("Tarea en segundo plano (%s) fallida: %s", what, finished.exception())

        task.add_done_callback(done)
        return task
