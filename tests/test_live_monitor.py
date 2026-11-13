"""Seguimiento de la partida: arranca y para con el estado del cliente, y tolera la carga."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any, Self

from lolcoach.builds.recommender import BuildRecommender
from lolcoach.bus import EventBus
from lolcoach.events import GameflowPhaseChanged, LiveGameEnded, LiveGameUpdated
from lolcoach.ingame.client import LiveClientUnavailable
from lolcoach.ingame.monitor import LiveGameMonitor
from lolcoach.models import GameflowPhase, Position

from .gamedata_fixture import FixtureClient, loaded_gamedata
from .harness import wait_until
from .live_factories import standard_game


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
