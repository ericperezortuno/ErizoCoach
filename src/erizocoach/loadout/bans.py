"""Baneos: a quién banear con el campeón que preseleccionas.

* Solo en la Grieta y mientras te queda tu baneo por hacer (reclutamiento, clasificatorias).
* Cuando mantienes el mismo campeón preseleccionado ``BAN_HOVER_DELAY`` segundos: si vas
  pasando de uno a otro, no se calcula nada.
* De las partidas de alto elo: los rivales de tu línea que más le ganan a tu campeón,
  ponderados por lo a menudo que te tocan. Lo que importa es cuántas victorias te da
  quitarlo: ``parte de tus partidas contra él × (tu media − lo que ganas contra él)``.
  Un counter muy duro que casi nadie juega vale menos que uno algo menos duro que sale
  en una de cada diez partidas.
* Con pocas partidas un porcentaje engaña, así que se mezcla con tu media
  (``PRIOR_WEIGHT`` partidas «de media», como en los counters).
* Nunca se sugiere banear a quien ya está baneado o elegido, ni lo que miran o han
  cogido tus aliados (quitarle el campeón a un compañero no ayuda).
* Se recalcula si cambia la selección (otro baneo, un aliado mira otro campeón) y se
  oculta cuando ya has baneado.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from typing import Any

from ..bus import EventBus
from ..champselect.models import ChampSelectSnapshot
from ..events import (
    BansUpdated,
    ChampSelectEnded,
    ChampSelectStarted,
    ChampSelectUpdated,
    ClientDisconnected,
    MyPickPreviewChanged,
)
from ..models import Position, QueueInfo
from .counters import MIN_GAMES, PRIOR_WEIGHT, MatchupStats
from .models import BanPick
from .positions import SUMMONERS_RIFT, PositionHint, PositionsLookup, resolve_my_position

log = logging.getLogger(__name__)

BAN_HOVER_DELAY = 3.0  # segundos con el mismo campeón preseleccionado
TOP_BANS = 3
NOTHING = BansUpdated(0, Position.NONE, ())


def bans_for(
    champion_id: int,
    position: Position,
    stats: MatchupStats,
    *,
    unavailable: Collection[int] = (),
    limit: int = TOP_BANS,
) -> tuple[BanPick, ...]:
    """Los rivales de ``position`` que más conviene quitarle a ``champion_id``."""
    matchups = stats.lane_matchups(champion_id, position)
    total = sum(games for _rival, games, _wins in matchups)
    if not total:
        return ()
    base = stats.lane_win_rate(champion_id, position) or 0.5
    scored: list[tuple[float, BanPick]] = []
    for rival, games, wins in matchups:
        if games < MIN_GAMES or rival == champion_id or rival in unavailable:
            continue
        rate = (wins + PRIOR_WEIGHT * base) / (games + PRIOR_WEIGHT)
        harm = base - rate
        if harm <= 0:
            continue  # no te gana: banearlo no te da nada
        share = games / total
        scored.append((share * harm, BanPick(rival, games, wins, round(rate, 4), round(share, 4))))
    scored.sort(key=lambda item: (-item[0], -item[1].games, item[1].champion_id))
    return tuple(pick for _value, pick in scored[:limit])


def unavailable_bans(snapshot: ChampSelectSnapshot) -> set[int]:
    """Lo que no tiene sentido banear: lo ya baneado o elegido y lo que miran tus aliados."""
    taken = {slot.champion_id for slot in snapshot.slots if slot.has_champion}
    hovered = {slot.intent_id for slot in snapshot.allies if slot.intent_id}
    return taken | hovered | set(snapshot.ally_bans) | set(snapshot.enemy_bans)


class BanService:
    """Publica ``BansUpdated`` con los baneos recomendados para tu preselección."""

    def __init__(
        self,
        bus: EventBus,
        positions_for: PositionsLookup,
        stats: MatchupStats,
        *,
        position_hint: PositionHint | None = None,
        delay: float = BAN_HOVER_DELAY,
    ) -> None:
        self._bus = bus
        self._positions_for = positions_for
        self._stats = stats
        self._hint = position_hint
        self._delay = delay
        self._queue: QueueInfo | None = None
        self._snapshot: ChampSelectSnapshot | None = None
        self._held = 0  # campeón preseleccionado el tiempo suficiente (0 = aún no)
        self._task: asyncio.Task[Any] | None = None
        self._last = NOTHING
        bus.subscribe(ChampSelectStarted, self._on_started)
        bus.subscribe(MyPickPreviewChanged, self._on_preview)
        bus.subscribe(ChampSelectUpdated, self._on_updated)
        bus.subscribe(ChampSelectEnded, self._on_ended)
        bus.subscribe(ClientDisconnected, self._on_ended)

    async def _on_started(self, event: ChampSelectStarted) -> None:
        self._cancel()
        self._queue, self._snapshot, self._held = event.queue, event.snapshot, 0
        await self._publish(NOTHING)

    async def _on_ended(self, _event: object) -> None:
        self._cancel()
        self._snapshot, self._held = None, 0
        await self._publish(NOTHING)

    async def _on_preview(self, event: MyPickPreviewChanged) -> None:
        self._cancel()
        self._snapshot, self._held = event.snapshot, 0
        await self._publish(NOTHING)  # el anterior ya no vale
        if event.champion_id and event.snapshot.my_ban_pending:
            self._task = asyncio.create_task(self._hold(event.champion_id))

    async def _on_updated(self, event: ChampSelectUpdated) -> None:
        self._snapshot = event.snapshot
        if self._held:
            await self._publish(self._compute())

    async def _hold(self, champion_id: int) -> None:
        await asyncio.sleep(self._delay)
        self._task = None
        self._held = champion_id
        log.info("Preselección mantenida: se calculan los baneos recomendados")
        await self._publish(self._compute())

    def _compute(self) -> BansUpdated:
        snapshot = self._snapshot
        me = snapshot.me if snapshot is not None else None
        if snapshot is None or me is None or not snapshot.my_ban_pending:
            return NOTHING
        if me.shown_champion != self._held:
            return NOTHING
        map_id = self._queue.map_id if self._queue and self._queue.map_id else SUMMONERS_RIFT
        position = resolve_my_position(
            me.position, self._held, map_id, self._positions_for, self._hint
        )
        if position == Position.NONE:
            return NOTHING
        picks = bans_for(self._held, position, self._stats, unavailable=unavailable_bans(snapshot))
        return BansUpdated(self._held, position, picks) if picks else NOTHING

    async def _publish(self, event: BansUpdated) -> None:
        if event != self._last:
            self._last = event
            await self._bus.publish(event)

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
