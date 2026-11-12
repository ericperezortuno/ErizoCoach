from __future__ import annotations

from lolcoach.bus import EventBus
from lolcoach.champselect.parser import parse_session
from lolcoach.champselect.tracker import ChampSelectTracker
from lolcoach.events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    MyChampionChanged,
    PicksFinalized,
)
from lolcoach.models import GameflowPhase

from .factories import AHRI, JINX, ZED, DraftScenario, aram_session

ALL_EVENTS = (
    ChampSelectStarted,
    ChampionLocked,
    MyChampionChanged,
    PicksFinalized,
    ChampSelectEnded,
)


def recorder(bus: EventBus) -> list[object]:
    events: list[object] = []
    for event_type in ALL_EVENTS:
        bus.subscribe(event_type, events.append)
    return events


async def test_full_draft_emits_each_event_once() -> None:
    bus = EventBus()
    events = recorder(bus)
    tracker = ChampSelectTracker(bus)
    scenario = DraftScenario()

    for picks in range(11):
        snapshot = parse_session(scenario.state(picks))
        await tracker.update(snapshot)
        await tracker.update(snapshot)  # duplicados (ticks del temporizador) no generan nada

    types = [type(e) for e in events]
    assert types.count(ChampSelectStarted) == 1
    assert types.count(ChampionLocked) == 10
    assert types.count(MyChampionChanged) == 1
    assert types.count(PicksFinalized) == 1
    assert types[-1] is PicksFinalized  # el último pick (enemigo) dispara la finalización

    mine = next(e for e in events if isinstance(e, MyChampionChanged))
    assert mine.champion_id == AHRI and mine.previous_champion_id == 0

    await tracker.end(GameflowPhase.GAME_START)
    ended = events[-1]
    assert isinstance(ended, ChampSelectEnded)
    assert ended.outcome == ChampSelectOutcome.GAME_STARTING
    assert not tracker.active


async def test_hovering_does_not_count_as_my_pick() -> None:
    bus = EventBus()
    events = recorder(bus)
    tracker = ChampSelectTracker(bus)
    await tracker.update(parse_session(DraftScenario().state(4, hover=ZED)))
    assert not any(isinstance(e, MyChampionChanged) for e in events)


async def test_trade_after_finalization_emits_my_champion_changed() -> None:
    bus = EventBus()
    events = recorder(bus)
    tracker = ChampSelectTracker(bus)
    data = DraftScenario().state(10)
    await tracker.update(parse_session(data))
    data["myTeam"][2]["championId"] = JINX
    data["myTeam"][3]["championId"] = AHRI
    await tracker.update(parse_session(data))

    changes = [e for e in events if isinstance(e, MyChampionChanged)]
    assert [(c.previous_champion_id, c.champion_id) for c in changes] == [(0, AHRI), (AHRI, JINX)]
    assert sum(isinstance(e, PicksFinalized) for e in events) == 1


async def test_dodge_then_new_champ_select() -> None:
    bus = EventBus()
    events = recorder(bus)
    tracker = ChampSelectTracker(bus)
    await tracker.update(parse_session(DraftScenario(game_id=1).state(5)))
    await tracker.end(GameflowPhase.MATCHMAKING)  # alguien hizo dodge: vuelta a la cola
    ended = events[-1]
    assert isinstance(ended, ChampSelectEnded) and ended.outcome == ChampSelectOutcome.CANCELLED

    await tracker.update(parse_session(DraftScenario(game_id=2).state(10)))
    assert sum(isinstance(e, ChampSelectStarted) for e in events) == 2
    assert sum(isinstance(e, PicksFinalized) for e in events) == 1


async def test_new_game_id_without_end_resets_state() -> None:
    bus = EventBus()
    events = recorder(bus)
    tracker = ChampSelectTracker(bus)
    await tracker.update(parse_session(DraftScenario(game_id=1).state(10)))
    await tracker.update(parse_session(DraftScenario(game_id=2).state(10)))
    assert sum(isinstance(e, ChampSelectStarted) for e in events) == 2
    assert sum(isinstance(e, PicksFinalized) for e in events) == 2


async def test_aram_bench_swap() -> None:
    bus = EventBus()
    events = recorder(bus)
    tracker = ChampSelectTracker(bus)
    await tracker.update(parse_session(aram_session(ZED)))
    await tracker.update(parse_session(aram_session(AHRI)))  # cambio desde el banquillo
    changes = [e for e in events if isinstance(e, MyChampionChanged)]
    assert [c.champion_id for c in changes] == [ZED, AHRI]
    assert sum(isinstance(e, PicksFinalized) for e in events) == 1


async def test_end_without_session_emits_nothing() -> None:
    bus = EventBus()
    events = recorder(bus)
    await ChampSelectTracker(bus).end(GameflowPhase.LOBBY)
    assert events == []
