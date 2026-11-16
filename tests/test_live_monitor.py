"""Seguimiento de la partida: arranca y para con el estado del cliente, y tolera la carga."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any, Self

from erizocoach.builds.recommender import BuildRecommender
from erizocoach.bus import EventBus
from erizocoach.events import GameflowPhaseChanged, LiveGameEnded, LiveGameUpdated
from erizocoach.ingame.client import LiveClientUnavailable
from erizocoach.ingame.monitor import LiveGameMonitor
from erizocoach.models import GameflowPhase, Position
from erizocoach.stats.aggregate import ChampionStats
from erizocoach.stats.source import StatsBuildSource

from .gamedata_fixture import FixtureClient, loaded_gamedata
from .harness import wait_until
from .live_factories import bot_game, standard_game


class FakeLiveClient:
    """Primero "cargando" (no disponible) y después la partida."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def all_game_data(self) -> Any:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class StubConnector:
    def __init__(self) -> None:
        self.client = FixtureClient()


async def test_monitor_follows_the_game_lifecycle() -> None:
    bus = EventBus()
    gd = loaded_gamedata()
    events: list[object] = []
    bus.subscribe(LiveGameUpdated, events.append)
    bus.subscribe(LiveGameEnded, events.append)
    live = FakeLiveClient([LiveClientUnavailable("cargando"), standard_game()])
    monitor = LiveGameMonitor(
        bus, StubConnector(), gd, ssl.create_default_context(), lambda: None,  # type: ignore[arg-type]
        poll_interval=0.01, live_client_factory=lambda: live,
    )  # fmt: skip

    await bus.publish(GameflowPhaseChanged(GameflowPhase.CHAMP_SELECT, GameflowPhase.GAME_START))
    assert monitor.running
    await wait_until(lambda: any(isinstance(e, LiveGameUpdated) for e in events))
    update = next(e for e in events if isinstance(e, LiveGameUpdated))
    # Sin build de la selección (app abierta a mitad): se calcula al vuelo
    assert update.plan is not None and update.plan.champion_id == 103
    assert update.advice is not None and update.advice.next_purchase is not None

    await bus.publish(GameflowPhaseChanged(GameflowPhase.IN_PROGRESS, GameflowPhase.END_OF_GAME))
    assert not monitor.running
    assert isinstance(events[-1], LiveGameEnded)


async def test_monitor_remembers_boots_in_the_role_quest_slot() -> None:
    """Las botas del tirador desaparecen de la API al completar su misión: siguen contando,
    y en la partida siguiente se empieza de cero."""
    bus = EventBus()
    gd = loaded_gamedata()
    plan = await BuildRecommender(gd).recommend_for(FixtureClient(), 145, Position.BOTTOM, [], 11)
    assert plan is not None and plan.boots == 3006
    updates: list[LiveGameUpdated] = []
    bus.subscribe(LiveGameUpdated, updates.append)
    with_boots = bot_game((1055, 6672, 3006), gold=400)
    quest_done = bot_game((1055, 6672), gold=700)
    live = FakeLiveClient([with_boots, quest_done])
    monitor = LiveGameMonitor(
        bus, StubConnector(), gd, ssl.create_default_context(), lambda: plan,  # type: ignore[arg-type]
        poll_interval=0.01, live_client_factory=lambda: live,
    )  # fmt: skip

    def boots_owned(update: LiveGameUpdated) -> bool:
        assert update.advice is not None
        return next(s for s in update.advice.build if s.item_id == 3006).owned

    await bus.publish(GameflowPhaseChanged(GameflowPhase.UNKNOWN, GameflowPhase.IN_PROGRESS))
    await wait_until(lambda: len(updates) >= 3)
    assert all(boots_owned(u) for u in updates)
    advice = updates[-1].advice
    assert advice is not None and advice.next_purchase is not None
    assert advice.next_purchase.item_id != 3006  # ya no pide las Grebas
    await bus.publish(GameflowPhaseChanged(GameflowPhase.IN_PROGRESS, GameflowPhase.END_OF_GAME))

    updates.clear()
    live.responses, live.calls = [quest_done], 0  # otra partida, sin botas a la vista
    await bus.publish(GameflowPhaseChanged(GameflowPhase.UNKNOWN, GameflowPhase.IN_PROGRESS))
    await wait_until(lambda: bool(updates))
    assert not boots_owned(updates[0])
    await monitor.stop()
    await asyncio.sleep(0)


async def test_monitor_switches_to_stats_once_they_load() -> None:
    """App abierta a mitad de partida: la build se calcula antes de que se carguen las
    estadísticas (con las reglas). Cuando llegan, la build pasa a ser la de alto elo."""
    bus = EventBus()
    gd = loaded_gamedata()
    source = StatsBuildSource(min_games=20)  # vacía: las estadísticas aún se están leyendo
    updates: list[LiveGameUpdated] = []
    bus.subscribe(LiveGameUpdated, updates.append)
    monitor = LiveGameMonitor(
        bus, StubConnector(), gd, ssl.create_default_context(), lambda: None,  # type: ignore[arg-type]
        poll_interval=0.01, live_client_factory=lambda: FakeLiveClient([standard_game()]),
        source=source,
    )  # fmt: skip
    await bus.publish(GameflowPhaseChanged(GameflowPhase.UNKNOWN, GameflowPhase.IN_PROGRESS))
    await wait_until(lambda: bool(updates))
    first = updates[-1].plan
    assert first is not None and "estadísticas" not in first.source  # reglas

    stats_core = (6655, 4645, 3089)
    entry = ChampionStats(103, "MIDDLE", 40, 20, (), 3020, 0.95, stats_core, ())
    source.update("16.22", 500, {(103, "MIDDLE"): entry})
    await wait_until(lambda: updates[-1].plan is not first)
    plan = updates[-1].plan
    assert plan is not None and plan.core == stats_core
    assert "40 partidas" in plan.source
    await monitor.stop()
    await asyncio.sleep(0)


async def test_monitor_uses_the_champ_select_build() -> None:
    bus = EventBus()
    gd = loaded_gamedata()
    plan = await BuildRecommender(gd).recommend_for(FixtureClient(), 103, Position.MIDDLE, [], 11)
    updates: list[LiveGameUpdated] = []
    bus.subscribe(LiveGameUpdated, updates.append)
    monitor = LiveGameMonitor(
        bus, StubConnector(), gd, ssl.create_default_context(), lambda: plan,  # type: ignore[arg-type]
        poll_interval=0.01, live_client_factory=lambda: FakeLiveClient([standard_game()]),
    )  # fmt: skip
    await bus.publish(GameflowPhaseChanged(GameflowPhase.UNKNOWN, GameflowPhase.IN_PROGRESS))
    await wait_until(lambda: bool(updates))
    assert updates[0].plan is plan
    await monitor.stop()
    await asyncio.sleep(0)
