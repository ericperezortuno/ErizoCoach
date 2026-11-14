"""Counters: qué campeones te puedes coger contra tu rival de línea.

* Solo en la Grieta y con una línea asignada (clasificatorias, reclutamiento): a ciegas no
  se sabe quién irá contra ti.
* Tu rival de línea: el enemigo que ya ha elegido y que, por campeón, más probablemente
  juega en tu posición (si no está claro, no se sugiere nada).
* De las partidas de alto elo descargadas: cómo le va a cada campeón en tu posición contra
  ese rival. Con pocas partidas un porcentaje engaña (3 de 3 no es un 100 %), así que se
  mezcla con lo que gana ese campeón en general (``PRIOR_WEIGHT`` partidas «de media»):
  hacen falta bastantes partidas para que un porcentaje alto lo sea de verdad.
* Solo campeones que puedes elegir ahora (tuyos, sin banear y sin coger).
* Cuando ya has bloqueado, se muestra cómo va tu enfrentamiento.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from typing import Protocol

from ..bus import EventBus
from ..champselect.models import ChampSelectSnapshot
from ..events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectStarted,
    CountersUpdated,
    MyChampionChanged,
)
from ..lcu.client import LcuClient, LcuError
from ..lcu.connector import LcuConnector
from ..models import Position, QueueInfo
from .models import LANE_CONFIDENCE, CounterPick
from .positions import SUMMONERS_RIFT, PositionsLookup, infer_positions, lane_opponent

log = logging.getLogger(__name__)

MIN_GAMES = 6  # partidas mínimas de un enfrentamiento para sugerirlo
PRIOR_WEIGHT = 20  # partidas «de media» que se mezclan con las reales
TOP_COUNTERS = 5
PICKABLE = "/lol-champ-select/v1/pickable-champion-ids"


class MatchupStats(Protocol):
    def lane_matchups(
        self, champion_id: int, position: Position
    ) -> tuple[tuple[int, int, int], ...]: ...

    def lane_win_rate(self, champion_id: int, position: Position) -> float | None: ...


def _smoothed(wins: int, games: int, prior: float) -> float:
    return (wins + PRIOR_WEIGHT * prior) / (games + PRIOR_WEIGHT)


def counters_for(
    enemy_id: int,
    position: Position,
    stats: MatchupStats,
    *,
    allowed: Collection[int] | None = None,
    unavailable: Collection[int] = (),
    limit: int = TOP_COUNTERS,
) -> tuple[CounterPick, ...]:
    """Los campeones a los que mejor les va contra ``enemy_id`` en ``position``."""
    picks: list[CounterPick] = []
    for champion_id, games, enemy_wins in stats.lane_matchups(enemy_id, position):
        if games < MIN_GAMES or champion_id in unavailable or champion_id == enemy_id:
            continue
        if allowed is not None and champion_id not in allowed:
            continue
        wins = games - enemy_wins
        prior = stats.lane_win_rate(champion_id, position) or 0.5
        rate = _smoothed(wins, games, prior)
        if rate > 0.5:
            picks.append(CounterPick(champion_id, games, wins, round(rate, 4)))
    picks.sort(key=lambda p: (-p.win_rate, -p.games, p.champion_id))
    return tuple(picks[:limit])


def matchup_for(
    champion_id: int, enemy_id: int, position: Position, stats: MatchupStats
) -> CounterPick | None:
    """Cómo le va a ``champion_id`` contra ``enemy_id`` (None sin partidas).

    Es el mismo enfrentamiento visto desde los dos lados: si no está en los datos de tu
    campeón, se mira en los del rival (tus victorias son sus derrotas)."""
    found = next(
        ((g, w) for rival, g, w in stats.lane_matchups(champion_id, position) if rival == enemy_id),
        None,
    )
    if found is None:
        found = next(
            ((g, g - w) for rival, g, w in stats.lane_matchups(enemy_id, position)
             if rival == champion_id),
            None,
        )  # fmt: skip
    if found is None:
        return None
    games, wins = found
    prior = stats.lane_win_rate(champion_id, position) or 0.5
    return CounterPick(champion_id, games, wins, round(_smoothed(wins, games, prior), 4))


def lane_rival(snapshot: ChampSelectSnapshot, positions_for: PositionsLookup) -> int:
    """El enemigo ya elegido que va a tu línea (0 si aún no se sabe o no está claro)."""
    me = snapshot.me
    if me is None or me.position not in (Position.TOP, Position.JUNGLE, Position.MIDDLE,
                                         Position.BOTTOM, Position.UTILITY):  # fmt: skip
        return 0
    enemies = [slot.champion_id for slot in snapshot.known_enemies]
    guess = lane_opponent(infer_positions(enemies, positions_for), me.position)
    return guess.champion_id if guess and guess.probability >= LANE_CONFIDENCE else 0


class CounterService:
    """Calcula los counters en la selección de campeones y los publica (``CountersUpdated``)."""

    def __init__(
        self,
        bus: EventBus,
        connector: LcuConnector,
        positions_for: PositionsLookup,
        stats: MatchupStats,
        *,
        client: Callable[[], LcuClient | None] | None = None,
    ) -> None:
        self._bus = bus
        self._positions_for = positions_for
        self._stats = stats
        self._client = client or (lambda: connector.client)
        self._queue: QueueInfo | None = None
        self._last: CountersUpdated | None = None
        bus.subscribe(ChampSelectStarted, self._on_started)
        bus.subscribe(ChampionLocked, self._on_locked)
        bus.subscribe(MyChampionChanged, self._on_mine)
        bus.subscribe(ChampSelectEnded, self._on_ended)

    async def _on_started(self, event: ChampSelectStarted) -> None:
        self._queue, self._last = event.queue, None
        await self._update(event.snapshot)

    async def _on_locked(self, event: ChampionLocked) -> None:
        await self._update(event.snapshot)

    async def _on_mine(self, event: MyChampionChanged) -> None:
        await self._update(event.snapshot)

    async def _on_ended(self, _event: ChampSelectEnded) -> None:
        self._last = None
        await self._bus.publish(CountersUpdated(Position.NONE, 0, ()))

    async def _update(self, snapshot: ChampSelectSnapshot) -> None:
        event = await self._compute(snapshot)
        if event != self._last:
            self._last = event
            await self._bus.publish(event)

    async def _compute(self, snapshot: ChampSelectSnapshot) -> CountersUpdated:
        me = snapshot.me
        map_id = self._queue.map_id if self._queue and self._queue.map_id else SUMMONERS_RIFT
        enemy = lane_rival(snapshot, self._positions_for) if map_id == SUMMONERS_RIFT else 0
        if me is None or not enemy:
            return CountersUpdated(Position.NONE, 0, ())
        position = me.position
        if me.locked and me.champion_id:  # ya has elegido: cómo va tu enfrentamiento
            mine = matchup_for(me.champion_id, enemy, position, self._stats)
            return CountersUpdated(position, enemy, (), mine=mine, locked=True)
        taken = {slot.champion_id for slot in snapshot.slots if slot.champion_id}
        unavailable = taken | set(snapshot.ally_bans) | set(snapshot.enemy_bans)
        picks = counters_for(
            enemy, position, self._stats,
            allowed=await self._pickable(), unavailable=unavailable,
        )  # fmt: skip
        return CountersUpdated(position, enemy, picks)

    async def _pickable(self) -> set[int] | None:
        """Los campeones que puedes elegir ahora (tuyos, sin banear ni coger)."""
        client = self._client()
        if client is None:
            return None
        try:
            data = await client.get(PICKABLE)
        except LcuError as exc:
            log.debug("Sin lista de campeones elegibles: %s", exc)
            return None
        if not isinstance(data, list):
            return None
        return {c for c in data if isinstance(c, int) and not isinstance(c, bool)}
