"""Montaje de la app completa contra el cliente simulado, para pruebas de punta a punta."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lolcoach.builds.service import BuildService
from lolcoach.bus import EventBus
from lolcoach.champselect.tracker import ChampSelectTracker
from lolcoach.config import Settings
from lolcoach.events import (
    BuildApplied,
    BuildReady,
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectStarted,
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    LoadoutApplied,
    LoadoutReady,
    MyChampionChanged,
    PicksFinalized,
)
from lolcoach.gamedata.catalog import GameData
from lolcoach.lcu import endpoints
from lolcoach.lcu.connector import LcuConnector
from lolcoach.lcu.discovery import LcuCredentials
from lolcoach.loadout.service import LoadoutService
from lolcoach.models import GameflowPhase
from lolcoach.monitor import ClientMonitor

from .factories import DraftScenario
from .fake_lcu import FakeLcu

EVENT_TYPES = (
    BuildReady,
    BuildApplied,
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    ChampSelectStarted,
    ChampionLocked,
    MyChampionChanged,
    PicksFinalized,
    ChampSelectEnded,
    LoadoutReady,
    LoadoutApplied,
)


class StaticLocator:
    def __init__(self, credentials: LcuCredentials) -> None:
        self.credentials = credentials

    def locate(self) -> LcuCredentials | None:
        return self.credentials


class Harness:
    """La app real (sin consola), conectada al cliente simulado."""

    def __init__(
        self,
        credentials: LcuCredentials,
        client_ssl: ssl.SSLContext,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.bus = EventBus()
        self.events: list[object] = []
        for event_type in EVENT_TYPES:
            self.bus.subscribe(event_type, self.events.append)
        self.connector = LcuConnector(
            self.bus,
            StaticLocator(credentials),
            client_ssl,
            discovery_interval=0.05,
            ready_poll_interval=0.05,
            ready_timeout=5,
        )
        self.gamedata = GameData()
        self.tracker = ChampSelectTracker(self.bus)
        self.monitor = ClientMonitor(self.bus, self.connector, self.gamedata, self.tracker)
        settings = settings or Settings(data_dir=Path("."))
        self.loadout = LoadoutService(
            self.bus, self.connector, self.gamedata, settings, page_check_delay=0.05
        )
        self.builds = BuildService(self.bus, self.connector, self.gamedata, settings)
        self._task: asyncio.Task[None] | None = None

    def of(self, event_type: type) -> list:
        return [e for e in self.events if isinstance(e, event_type)]

    async def __aenter__(self) -> Harness:
        self._task = asyncio.create_task(self.connector.run())
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def credentials_for(fake: FakeLcu, password: str | None = None) -> LcuCredentials:
    return LcuCredentials(port=fake.port, password=password or fake.password)


async def play_draft(
    fake: FakeLcu,
    app: Harness,
    *,
    my_spells: tuple[int, int] = (4, 14),
    until: tuple[type, ...] = (LoadoutApplied, BuildApplied),
) -> dict[str, Any]:
    """Juega una selección en draft completa (Ahri medio contra Zed).

    Espera a que se hayan aplicado runas/hechizos y build (``until``).
    """
    await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
    await fake.set_phase("ChampSelect")
    await wait_until(lambda: endpoints.CHAMP_SELECT_SESSION in fake.requests)
    scenario = DraftScenario()
    state: dict[str, Any] = {}
    for picks in range(11):
        state = scenario.state(picks)
        me = state["myTeam"][2]
        me["spell1Id"], me["spell2Id"] = my_spells
        await fake.set_session(state)
    await wait_until(lambda: all(app.of(event_type) for event_type in until))
    return state
