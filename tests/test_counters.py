"""Counters contra tu rival de línea, de los enfrentamientos de las partidas de alto elo."""

from __future__ import annotations

import dataclasses
from typing import Any

from erizocoach.bus import EventBus
from erizocoach.champselect.models import ChampSelectPhase, ChampSelectSnapshot, PlayerSlot
from erizocoach.events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    CountersUpdated,
    MyChampionChanged,
)
from erizocoach.lcu.client import LcuError
from erizocoach.loadout.counters import (
    MIN_GAMES,
    PRIOR_WEIGHT,
    CounterService,
    counters_for,
    lane_rival,
    matchup_for,
)
from erizocoach.models import Position, QueueInfo
from erizocoach.stats.aggregate import ChampionStats, lane_matchups, with_matchups
from erizocoach.stats.extract import PlayerBuild
from erizocoach.stats.pack import decode_pack, encode_pack, is_sane
from erizocoach.stats.source import StatsBuildSource
from erizocoach.ui.web.presenter import UiPresenter

from .gamedata_fixture import loaded_gamedata

AHRI, ZED, YASUO, LUX, SYNDRA, ANNIE = 103, 238, 157, 99, 134, 1
DARIUS, GRAVES, KAISA, SORAKA, GAREN = 122, 104, 145, 16, 86
RANKED = QueueInfo(420, "Clasificatoria", "CLASSIC", 11)
POSITIONS_BY_CHAMPION = {
    ZED: (Position.MIDDLE,), AHRI: (Position.MIDDLE,), YASUO: (Position.MIDDLE, Position.TOP),
    DARIUS: (Position.TOP,), GRAVES: (Position.JUNGLE,), KAISA: (Position.BOTTOM,),
    SORAKA: (Position.UTILITY,), GAREN: (Position.TOP,),
}  # fmt: skip


def positions_for(champion_id: int) -> tuple[Position, ...]:
    return POSITIONS_BY_CHAMPION.get(champion_id, ())


def build(champion: int, position: str, win: bool) -> PlayerBuild:
    return PlayerBuild(champion, position, win, ())


def match(*seats: tuple[int, int, str]) -> list[PlayerBuild]:
    """(campeón ganador, campeón perdedor, posición) por línea."""
    return [b for w, lo, pos in seats for b in (build(w, pos, True), build(lo, pos, False))]


def entry(champion: int, position: str, games: int, wins: int, **extra: Any) -> ChampionStats:
    return ChampionStats(champion, position, games, wins, (), None, 0.0, (), (), **extra)


def source_with(
    matchups: dict[tuple[int, str], tuple[tuple[int, int, int], ...]],
    overall: dict[tuple[int, str], tuple[int, int]],
) -> StatsBuildSource:
    source = StatsBuildSource(min_games=20)
    keys = set(matchups) | set(overall)
    stats = {
        key: entry(*key, *overall.get(key, (0, 0)), matchups=matchups.get(key, ())) for key in keys
    }
    source.update("16.22", 1000, stats)
    return source


# --- Enfrentamientos en las estadísticas ---------------------------------------------


def test_each_player_is_paired_with_the_rival_of_their_lane() -> None:
    games = [
        match((AHRI, ZED, "MIDDLE"), (DARIUS, GAREN, "TOP")),
        match((ZED, AHRI, "MIDDLE")),
        match((AHRI, ZED, "MIDDLE")),
        # Dos en medio del mismo equipo (posición dudosa): esa línea no cuenta
        [build(AHRI, "MIDDLE", True), build(LUX, "MIDDLE", True), build(ZED, "MIDDLE", False)],
        [build(AHRI, "", True), build(ZED, "", False)],  # sin posición: tampoco
    ]
    counts = lane_matchups(games)
    assert counts[(AHRI, "MIDDLE")][ZED] == [3, 2]
    assert counts[(ZED, "MIDDLE")][AHRI] == [3, 1]
    assert counts[(DARIUS, "TOP")][GAREN] == [1, 1] and LUX not in counts[(AHRI, "MIDDLE")]


def test_matchups_are_stored_with_two_games_or_more() -> None:
    games = [match((AHRI, ZED, "MIDDLE"))] * 3 + [match((AHRI, LUX, "MIDDLE"))]
    stats = {
        (AHRI, "MIDDLE"): entry(AHRI, "MIDDLE", 4, 4),
        (LUX, "MIDDLE"): entry(LUX, "MIDDLE", 1, 0),
    }
    enriched = with_matchups(stats, lane_matchups(games))
    assert enriched[(AHRI, "MIDDLE")].matchups == ((ZED, 3, 3),)
    assert enriched[(LUX, "MIDDLE")].matchups == ()


def test_matchups_travel_in_the_shared_pack_and_are_validated() -> None:
    good = entry(AHRI, "MIDDLE", 50, 27, matchups=((ZED, 12, 8), (YASUO, 6, 2)))
    _patch, _matches, stats = decode_pack(encode_pack(("16.22", 500, {(AHRI, "MIDDLE"): good})))
    assert stats[(AHRI, "MIDDLE")].matchups == ((ZED, 12, 8), (YASUO, 6, 2))
    assert not is_sane(dataclasses.replace(good, matchups=((ZED, 5, 6),)))  # más victorias
    assert not is_sane(dataclasses.replace(good, matchups=((ZED, 60, 1),)))  # más que sus partidas
    assert not is_sane(dataclasses.replace(good, matchups=((0, 5, 1),)))


# --- Qué te puedes coger ---------------------------------------------------------------


def zed_matchups() -> StatsBuildSource:
    # Desde Zed: (rival, partidas, victorias de Zed)
    return source_with(
        {
            (ZED, "MIDDLE"): (
                (AHRI, 15, 3),  # Ahri gana 12 de 15
                (LUX, 3, 0),  # Lux gana 3 de 3: pocas partidas
                (SYNDRA, 40, 18),  # Syndra gana 22 de 40
                (YASUO, 20, 14),  # Yasuo pierde
                (ANNIE, 30, 12),  # Annie gana 18 de 30
            ),
            (AHRI, "MIDDLE"): ((ZED, 15, 12),),
        },
        {
            (AHRI, "MIDDLE"): (300, 150), (SYNDRA, "MIDDLE"): (300, 150),
            (YASUO, "MIDDLE"): (300, 150), (ANNIE, "MIDDLE"): (300, 150),
            (LUX, "MIDDLE"): (300, 150),
        },
    )  # fmt: skip


def test_counters_are_the_champions_that_beat_your_rival() -> None:
    picks = counters_for(ZED, Position.MIDDLE, zed_matchups())
    assert [p.champion_id for p in picks] == [AHRI, ANNIE, SYNDRA]
    ahri = picks[0]
    assert (ahri.games, ahri.wins) == (15, 12)
    # 12 de 15 (80 %) mezclado con su media (50 %, como 20 partidas): 63 %
    assert ahri.win_rate == round((12 + PRIOR_WEIGHT * 0.5) / (15 + PRIOR_WEIGHT), 4)
    assert all(p.games >= MIN_GAMES for p in picks)  # 3 de 3 no basta
    assert YASUO not in [p.champion_id for p in picks]  # pierde: no es un counter


def test_only_champions_you_can_pick_are_suggested() -> None:
    source = zed_matchups()
    picks = counters_for(ZED, Position.MIDDLE, source, allowed={SYNDRA, ANNIE, YASUO})
    assert [p.champion_id for p in picks] == [ANNIE, SYNDRA]
    picks = counters_for(ZED, Position.MIDDLE, source, unavailable={AHRI})
    assert AHRI not in [p.champion_id for p in picks]


def test_your_matchup_once_you_have_picked() -> None:
    mine = matchup_for(AHRI, ZED, Position.MIDDLE, zed_matchups())
    assert mine is not None and (mine.games, mine.wins) == (15, 12)
    assert matchup_for(AHRI, YASUO, Position.MIDDLE, zed_matchups()) is None


# --- En la selección de campeones ------------------------------------------------------


def slot(cell: int, champion: int = 0, *, ally: bool = True, me: bool = False,
         position: Position = Position.NONE, locked: bool = True) -> PlayerSlot:  # fmt: skip
    return PlayerSlot(cell, ally, me, champion, locked and champion > 0, position)


def draft(
    *enemies: int,
    mine: int = 0,
    my_position: Position = Position.MIDDLE,
    bans: tuple[int, ...] = (),
) -> ChampSelectSnapshot:
    team = (
        slot(0, mine, me=True, position=my_position),
        slot(1, position=Position.TOP),
    )
    theirs = tuple(slot(5 + n, c, ally=False) for n, c in enumerate(enemies))
    return ChampSelectSnapshot(1, ChampSelectPhase.BAN_PICK, team, theirs, ally_bans=bans)


def test_your_lane_rival_is_the_enemy_who_plays_your_position() -> None:
    assert lane_rival(draft(DARIUS, ZED, GRAVES), positions_for) == ZED
    assert lane_rival(draft(DARIUS, GRAVES), positions_for) == 0  # aún no ha elegido
    assert lane_rival(draft(ZED, my_position=Position.NONE), positions_for) == 0  # a ciegas


class FakeClient:
    def __init__(self, pickable: list[int] | None) -> None:
        self.pickable = pickable

    async def get(self, path: str) -> Any:
        if self.pickable is None:
            raise LcuError("GET", path, 404)
        return self.pickable


def service(pickable: list[int] | None) -> tuple[EventBus, list[CountersUpdated]]:
    bus = EventBus()
    seen: list[CountersUpdated] = []
    bus.subscribe(CountersUpdated, seen.append)
    CounterService(
        bus,
        None,
        positions_for,
        zed_matchups(),
        client=lambda: FakeClient(pickable),  # type: ignore[arg-type]
    )
    return bus, seen


async def test_counters_appear_when_your_rival_picks() -> None:
    bus, seen = service([AHRI, SYNDRA, ANNIE, YASUO, LUX])
    await bus.publish(ChampSelectStarted(draft(), RANKED))
    assert seen[-1].enemy_id == 0  # nadie de tu línea todavía
    snapshot = draft(DARIUS, ZED, bans=(AHRI,))
    await bus.publish(ChampionLocked(snapshot.enemies[1], snapshot))
    event = seen[-1]
    assert event.enemy_id == ZED and event.position == Position.MIDDLE
    assert [p.champion_id for p in event.picks] == [ANNIE, SYNDRA]  # Ahri, baneada

    count = len(seen)
    await bus.publish(ChampionLocked(snapshot.enemies[0], snapshot))  # nada nuevo: no se repite
    assert len(seen) == count

    locked = draft(DARIUS, ZED, mine=SYNDRA)
    await bus.publish(MyChampionChanged(SYNDRA, 0, locked))
    assert seen[-1].locked and seen[-1].picks == () and seen[-1].mine is not None
    assert (seen[-1].mine.games, seen[-1].mine.wins) == (40, 22)

    await bus.publish(ChampSelectEnded(ChampSelectOutcome.GAME_STARTING, locked, RANKED))
    assert seen[-1].enemy_id == 0


async def test_without_the_pickable_list_bans_and_picks_are_still_left_out() -> None:
    bus, seen = service(None)
    snapshot = draft(ZED, bans=(ANNIE,))
    await bus.publish(ChampSelectStarted(snapshot, RANKED))
    assert [p.champion_id for p in seen[-1].picks] == [AHRI, SYNDRA]


async def test_no_counters_outside_the_rift() -> None:
    bus, seen = service([AHRI])
    await bus.publish(ChampSelectStarted(draft(ZED), QueueInfo(450, "ARAM", "ARAM", 12)))
    assert seen == [] or seen[-1].enemy_id == 0


# --- En la ventana ---------------------------------------------------------------------


async def test_the_window_shows_the_counters() -> None:
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    picks = counters_for(ZED, Position.MIDDLE, zed_matchups())
    await presenter._on_counters(CountersUpdated(Position.MIDDLE, ZED, picks))
    shown = presenter.state["counters"]
    assert shown["enemy"]["name"] == "Zed" and shown["enemy"]["position"] == "Medio"
    assert shown["picks"][0] == {"id": AHRI, "name": "Ahri", "winRate": 63, "games": 15, "wins": 12}
    assert not shown["locked"] and shown["mine"] is None

    await presenter._on_counters(CountersUpdated(Position.NONE, 0, ()))
    assert presenter.state["counters"] is None


async def test_counters_survive_the_start_of_champ_select() -> None:
    """Como en la app: el módulo de counters se avisa antes que la ventana. Si al empezar
    (app abierta a mitad de selección) el rival ya había elegido, la ventana los borraba."""
    bus = EventBus()
    CounterService(
        bus,
        None,
        positions_for,
        zed_matchups(),
        client=lambda: FakeClient(None),  # type: ignore[arg-type]
    )
    presenter = UiPresenter(bus, loaded_gamedata())
    await bus.publish(ChampSelectStarted(draft(DARIUS, ZED), RANKED))
    assert presenter.state["counters"] is not None
    assert presenter.state["counters"]["enemy"]["name"] == "Zed"
