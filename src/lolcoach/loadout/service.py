"""Orquesta la fase 2: cuándo calcular y aplicar las runas y los hechizos.

* Mientras se bloquean campeones: se descargan por adelantado los datos
  necesarios (perfiles, recomendaciones), en segundo plano.
* Mientras preseleccionas o miras un campeón: se calculan sus runas y hechizos para
  enseñarlos ya (``LoadoutPreview``). Si te quedas **5 segundos** en él, se ponen en el
  cliente (provisionales).
* Al hacerse el último pick (``PicksFinalized``): se calculan con todos los rivales y se
  aplican (ajustando las provisionales).
* Si después cambia tu campeón (intercambio o banquillo de ARAM): se vuelve a aplicar.
* Desde la ventana puedes cambiar las runas (se escriben en tu página de Erizo Coach) y los
  hechizos.
* Lo que cambies tú (en la ventana o en el cliente) se respeta para ese campeón: la app
  no lo vuelve a tocar salvo que cambies de campeón. Antes de escribir, se comprueba en
  el cliente si has cambiado algo.

Cada vez que cambia lo que hay en el cliente se publica ``LoadoutInClient``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
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
    LoadoutInClient,
    LoadoutPreview,
    LoadoutReady,
    MyChampionChanged,
    MyPickPreviewChanged,
    PicksFinalized,
    RunesBy,
    RunesEditRequested,
    SpellsEditRequested,
)
from ..gamedata.catalog import GameData
from ..gamedata.runes import RunePage, validate_page
from ..lcu import endpoints
from ..lcu.client import LcuClient, LcuError
from ..lcu.connector import LcuConnector
from ..models import Position, QueueInfo
from ..util import as_dict, as_int, as_list
from .applier import LoadoutApplier, NoRunePageSlot, PageInfo, page_title
from .models import Loadout, LoadoutStats
from .positions import PositionHint
from .recommender import LoadoutRecommender
from .rules import SUMMONERS_RIFT, SmiteRule, smite_problem, smite_rule

log = logging.getLogger(__name__)

# El cliente a veces activa su página temporal justo al bloquear: lo comprobamos poco después.
PAGE_CHECK_DELAY = 1.5
# Al pasar de un campeón a otro rápido, solo se calcula el último en el que te paras.
PREVIEW_DELAY = 0.3
# Si sigues en el mismo campeón este tiempo, sus runas y hechizos se ponen ya.
HOVER_APPLY_DELAY = 5.0
NO_PAGE_SLOT = (
    "No hay hueco para la página «Erizo Coach»: renombra una de tus páginas a «Erizo Coach» "
    "(o borra una) y se usará esa. Pon las runas a mano esta vez."
)

SnapshotSource = Callable[[], ChampSelectSnapshot | None]


@dataclass(frozen=True, slots=True)
class _Runes:
    champion_id: int
    page: RunePage
    by: RunesBy
    page_id: int = 0  # página de Erizo Coach donde se escribió (0 = no la escribimos nosotros)


@dataclass(frozen=True, slots=True)
class _Spells:
    champion_id: int
    spells: tuple[int, int]
    by: RunesBy


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
        hover_apply_delay: float = HOVER_APPLY_DELAY,
        position_hint: PositionHint | None = None,
        stats: LoadoutStats | None = None,
        current_snapshot: SnapshotSource | None = None,
    ) -> None:
        self._bus = bus
        self._connector = connector
        self._gamedata = gamedata
        self._settings = settings
        self._page_check_delay = page_check_delay
        self._preview_delay = preview_delay
        self._hover_apply_delay = max(hover_apply_delay, preview_delay)
        self._current_snapshot = current_snapshot or (lambda: None)
        self._preview_task: asyncio.Task[Any] | None = None
        self._recommender = LoadoutRecommender(gamedata, position_hint, stats)
        self._applier = LoadoutApplier()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._write_lock = asyncio.Lock()
        self._generation = 0
        self._queue: QueueInfo | None = None
        self._finalized = False
        self._final_champion = 0
        self._last_snapshot: ChampSelectSnapshot | None = None
        self._runes: _Runes | None = None  # lo que hay en el cliente
        self._spells: _Spells | None = None

        bus.subscribe(ChampSelectStarted, self._on_started)
        bus.subscribe(ChampionLocked, self._on_locked)
        bus.subscribe(MyChampionChanged, self._on_my_champion)
        bus.subscribe(MyPickPreviewChanged, self._on_preview)
        bus.subscribe(PicksFinalized, self._on_finalized)
        bus.subscribe(RunesEditRequested, self._on_edit)
        bus.subscribe(SpellsEditRequested, self._on_spells)
        bus.subscribe(ChampSelectEnded, self._on_end)
        bus.subscribe(ClientDisconnected, self._on_end)

    # --- Eventos --------------------------------------------------------------------

    def _on_started(self, event: ChampSelectStarted) -> None:
        self._reset()
        self._queue = event.queue
        self._last_snapshot = event.snapshot

    def _on_end(self, _event: object) -> None:
        self._reset()
        # Las precargas y comprobaciones pendientes ya no sirven: se cancelan.
        for task in list(self._tasks):
            task.cancel()

    def _on_locked(self, event: ChampionLocked) -> None:
        self._last_snapshot = event.snapshot
        client = self._connector.client
        if client is not None:
            self._spawn(self._gamedata.profile(client, event.slot.champion_id), "perfil")

    async def _on_my_champion(self, event: MyChampionChanged) -> None:
        self._last_snapshot = event.snapshot
        if self._finalized:
            await self._run(event.snapshot)  # intercambio o banquillo tras el último pick
            return
        client = self._connector.client
        if client is not None:
            self._spawn(self._recommender.prefetch(client, event.snapshot, self._queue), "precarga")

    def _on_preview(self, event: MyPickPreviewChanged) -> None:
        self._last_snapshot = event.snapshot
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
        if generation != self._generation or self._finalized:
            return
        await self._bus.publish(LoadoutPreview(event.champion_id, loadout))
        if loadout is None:
            return
        # Sigues en el mismo campeón: se ponen ya (provisionales hasta el último pick)
        await asyncio.sleep(self._hover_apply_delay - self._preview_delay)
        client = self._connector.client
        if client is None or generation != self._generation or self._finalized:
            return
        async with self._write_lock:
            if generation == self._generation and not self._finalized:
                log.info("Preselección mantenida: se ponen ya sus runas y hechizos")
                await self._apply(client, loadout, provisional=True)

    async def _on_finalized(self, event: PicksFinalized) -> None:
        self._last_snapshot = event.snapshot
        self._finalized = True
        self._queue = event.queue or self._queue
        if self._preview_task is not None:
            self._preview_task.cancel()  # ya no hace falta la provisional
        await self._run(event.snapshot)

    async def _on_edit(self, event: RunesEditRequested) -> None:
        """Has cambiado las runas en la ventana: se escriben en tu página de Erizo Coach."""
        champion = self._target_champion()
        client = self._connector.client
        problems = validate_page(event.page, self._gamedata.styles)
        problem = ""
        if client is None:
            problem = "Sin conexión con el cliente de LoL: no se han podido cambiar las runas."
        elif not champion:
            problem = "Las runas se cambian durante la selección, con un campeón elegido."
        elif problems:
            problem = f"Esa página de runas no es válida: {problems[0]}."
        if problem or client is None:
            await self._publish_state(champion, problem=problem)
            return
        async with self._write_lock:
            title = page_title(self._gamedata.champion_name(champion))
            try:
                page_id = await self._applier.apply_runes(client, event.page, title)
            except NoRunePageSlot:
                await self._publish_state(champion, problem=NO_PAGE_SLOT)
                return
            except LcuError as exc:
                await self._publish_state(champion, problem=f"No se pudieron cambiar: {exc}")
                return
            self._runes = _Runes(champion, event.page, RunesBy.YOU_IN_APP, page_id)
            log.info("Runas cambiadas desde la ventana (%s)", title)
            await self._publish_state(champion, page_name=title)

    async def _on_spells(self, event: SpellsEditRequested) -> None:
        """Has cambiado los hechizos en la ventana: se ponen en tu selección."""
        champion = self._target_champion()
        client = self._connector.client
        mode = self._queue.game_mode if self._queue else ""
        allowed = {s.id for s in self._gamedata.spells_for(mode)}
        first, second = event.spells
        problem = ""
        if client is None:
            problem = "Sin conexión con el cliente de LoL: no se han podido cambiar los hechizos."
        elif not champion:
            problem = "Los hechizos se cambian durante la selección, con un campeón elegido."
        elif first == second or first not in allowed or second not in allowed:
            problem = "Esos hechizos no se pueden llevar juntos en este modo."
        else:  # Aplastar: obligatorio en la jungla, prohibido en las demás posiciones
            problem = smite_problem(event.spells, self._smite_rule())
        if problem or client is None:
            await self._publish_state(champion, problem=problem)
            return
        async with self._write_lock:
            try:
                await self._applier.apply_spells(client, event.spells)
            except LcuError as exc:
                await self._publish_state(champion, problem=f"No se pudieron cambiar: {exc}")
                return
            self._spells = _Spells(champion, event.spells, RunesBy.YOU_IN_APP)
            log.info("Hechizos cambiados desde la ventana")
            await self._publish_state(champion)

    # --- Cálculo y aplicación ---------------------------------------------------------

    async def _run(self, snapshot: ChampSelectSnapshot) -> None:
        client = self._connector.client
        me = snapshot.me
        if client is None or me is None or not me.has_champion:
            return
        if me.champion_id == self._final_champion:
            return  # ya aplicado para este campeón: respetamos tus cambios manuales
        loadout = await self._recommender.recommend(client, snapshot, self._queue)
        if loadout is None:
            return
        self._final_champion = me.champion_id
        await self._bus.publish(LoadoutReady(loadout))
        async with self._write_lock:
            await self._apply(client, loadout, provisional=False)

    async def _apply(self, client: LcuClient, loadout: Loadout, *, provisional: bool) -> None:
        """Escribe lo que no hayas cambiado tú y publica el resultado (con el lock tomado)."""
        champion = loadout.champion_id
        by = RunesBy.PREVIEW if provisional else RunesBy.COACH
        problems: list[str] = []
        page_name: str | None = None
        runes_applied = spells_applied = False

        await self._notice_manual_runes(client, champion)
        if loadout.runes is not None and self._settings.auto_runes:
            mine = self._runes
            if mine is not None and mine.champion_id == champion and mine.by.is_yours:
                pass  # las has cambiado tú: no se tocan
            else:
                title = page_title(self._gamedata.champion_name(champion))
                try:
                    page_id = await self._applier.apply_runes(client, loadout.runes, title)
                except NoRunePageSlot:
                    problems.append(NO_PAGE_SLOT)
                except LcuError as exc:
                    problems.append(f"No se pudieron poner las runas: {exc}")
                else:
                    runes_applied, page_name = True, title
                    self._runes = _Runes(champion, loadout.runes, by, page_id)
                    self._spawn(self._check_page(self._generation, page_id), "comprobar página")

        current = self._snapshot_me_spells()
        self._notice_manual_spells(champion, current)
        if loadout.spells is not None and self._settings.auto_spells:
            chosen = self._spells
            if chosen is not None and chosen.champion_id == champion and chosen.by.is_yours:
                pass  # los has cambiado tú: no se tocan
            elif current == loadout.spells:
                spells_applied = True  # ya los tenías
                self._spells = _Spells(champion, loadout.spells, by)
            else:
                try:
                    await self._applier.apply_spells(client, loadout.spells)
                except LcuError as exc:
                    problems.append(f"No se pudieron poner los hechizos: {exc}")
                else:
                    spells_applied = True
                    self._spells = _Spells(champion, loadout.spells, by)

        await self._bus.publish(
            LoadoutApplied(
                loadout, page_name, runes_applied, spells_applied, tuple(problems), provisional
            )
        )
        await self._publish_state(
            champion,
            page_name=page_name,
            problem=problems[0] if problems else "",
        )

    async def _notice_manual_runes(self, client: LcuClient, champion: int) -> None:
        """¿Has cambiado a mano en el cliente la página que pusimos para este campeón?"""
        ours = self._runes
        if ours is None or ours.champion_id != champion or ours.by == RunesBy.YOU_IN_CLIENT:
            return
        try:
            raw = as_dict(await client.get(endpoints.PERK_CURRENT_PAGE))
        except LcuError:
            return
        info = PageInfo.from_json(raw)
        if info.temporary:
            return  # la página temporal la pone el cliente, no tú: se recupera la nuestra
        page = _page_from_api(raw)
        if info.id == ours.page_id and page == ours.page:
            return
        log.info("Has cambiado las runas en el cliente: no se tocan para este campeón")
        self._runes = _Runes(champion, page or ours.page, RunesBy.YOU_IN_CLIENT)

    def _notice_manual_spells(self, champion: int, current: tuple[int, int] | None) -> None:
        ours = self._spells
        if ours is None or ours.champion_id != champion or ours.by.is_yours or current is None:
            return
        if current != ours.spells:
            log.info("Has cambiado los hechizos en el cliente: no se tocan para este campeón")
            self._spells = _Spells(champion, current, RunesBy.YOU_IN_CLIENT)

    async def _publish_state(
        self, champion: int, *, page_name: str | None = None, problem: str = ""
    ) -> None:
        runes = self._runes if self._runes and self._runes.champion_id == champion else None
        spells = self._spells if self._spells and self._spells.champion_id == champion else None
        await self._bus.publish(
            LoadoutInClient(
                champion_id=champion,
                runes=runes.page if runes else None,
                spells=spells.spells if spells else None,
                runes_by=runes.by if runes else RunesBy.NOBODY,
                spells_by=spells.by if spells else RunesBy.NOBODY,
                page_name=page_name,
                problem=problem,
            )
        )

    async def _check_page(self, generation: int, page_id: int) -> None:
        await asyncio.sleep(self._page_check_delay)
        client = self._connector.client
        if client is None or generation != self._generation:
            return
        if await self._applier.ensure_current_page(client, page_id):
            log.info("El cliente había activado su página temporal; se ha vuelto a poner la tuya.")

    # --- Utilidades ---------------------------------------------------------------------

    def _snapshot(self) -> ChampSelectSnapshot | None:
        return self._current_snapshot() or self._last_snapshot

    def _snapshot_me_spells(self) -> tuple[int, int] | None:
        snapshot = self._snapshot()
        me = snapshot.me if snapshot else None
        return (me.spell1_id, me.spell2_id) if me is not None else None

    def _smite_rule(self) -> SmiteRule:
        """La regla de Aplastar con tu posición asignada (a ciegas no hay: la decides tú)."""
        snapshot = self._snapshot()
        me = snapshot.me if snapshot else None
        map_id = self._queue.map_id if self._queue and self._queue.map_id else SUMMONERS_RIFT
        return smite_rule(me.position if me else Position.NONE, map_id)

    def _target_champion(self) -> int:
        """El campeón para el que son las runas ahora: el bloqueado o el que miras."""
        snapshot = self._snapshot()
        me = snapshot.me if snapshot else None
        return me.shown_champion if me is not None else 0

    def _reset(self) -> None:
        self._generation += 1
        self._preview_task = None
        self._queue = None
        self._finalized = False
        self._final_champion = 0
        self._last_snapshot = None
        self._runes = None
        self._spells = None

    def _spawn(self, coroutine: Coroutine[Any, Any, Any], what: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def done(finished: asyncio.Task[Any]) -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                log.debug("Tarea en segundo plano (%s) fallida: %s", what, finished.exception())

        task.add_done_callback(done)
        return task


def _page_from_api(raw: dict[str, Any]) -> RunePage | None:
    perks = tuple(as_int(p) for p in as_list(raw.get("selectedPerkIds")))
    if not perks:
        return None
    return RunePage(as_int(raw.get("primaryStyleId")), as_int(raw.get("subStyleId")), perks)
