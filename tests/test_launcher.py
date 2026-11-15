"""Botón «Abrir League of Legends»: se abre el cliente de Riot de verdad, y solo ese."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lolcoach.bus import EventBus
from lolcoach.events import (
    ClientConnected,
    ClientDisconnected,
    ClientLaunchStatus,
    LaunchClientRequested,
    LaunchState,
)
from lolcoach.lcu.launcher import (
    LAUNCH_ARGS,
    ClientLauncher,
    find_riot_client,
)
from lolcoach.models import ClientInfo
from lolcoach.ui.web.presenter import UiPresenter

from .gamedata_fixture import loaded_gamedata


def riot_install(tmp_path: Path, name: str = "Riot Client") -> Path:
    exe = tmp_path / name / "RiotClientServices.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    return exe


def installs_file(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "RiotClientInstalls.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def real_layout(tmp_path: Path, exe: Path) -> Path:
    """Como el archivo real que escribe el instalador de Riot."""
    client = exe.as_posix()
    return installs_file(tmp_path, {
        "associated_client": {
            "D:/Riot Games/League of Legends/": client,
            "D:/Riot Games/VALORANT/live/": "D:/otro/RiotClientServices.exe",
        },
        "patchlines": {"KeystoneFoundationLiveWin": client},
        "rc_default": client,
        "rc_live": client,
    })  # fmt: skip


# --- Dónde está el cliente de Riot ---------------------------------------------------------


def test_finds_the_client_associated_with_league(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    assert find_riot_client(real_layout(tmp_path, exe)) == exe


def test_your_league_folder_wins(tmp_path: Path) -> None:
    mine, other = riot_install(tmp_path, "mio"), riot_install(tmp_path, "otro")
    path = installs_file(tmp_path, {
        "associated_client": {
            "C:/Juegos/League of Legends/": other.as_posix(),
            "E:\\LoL\\": str(mine),
        },
        "rc_live": other.as_posix(),
    })  # fmt: skip
    assert find_riot_client(path, league_dir=Path("e:/lol")) == mine


def test_falls_back_to_the_default_riot_client(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    path = installs_file(tmp_path, {"associated_client": {}, "rc_default": str(exe)})
    assert find_riot_client(path) == exe


@pytest.mark.parametrize(
    "client",
    [
        "C:/Windows/System32/cmd.exe",  # no es el cliente de Riot
        "RiotClientServices.exe",  # ruta relativa
        "D:/no/existe/RiotClientServices.exe",
        42,
    ],
)
def test_only_an_existing_riot_client_is_accepted(tmp_path: Path, client: object) -> None:
    path = installs_file(tmp_path, {"rc_live": client, "rc_default": client})
    assert find_riot_client(path) is None


def test_missing_or_broken_file(tmp_path: Path) -> None:
    assert find_riot_client(tmp_path / "no-existe.json") is None
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json", encoding="utf-8")
    assert find_riot_client(broken) is None
    assert find_riot_client(installs_file(tmp_path, ["lista"])) is None


# --- Abrir --------------------------------------------------------------------------------


class Recorder:
    def __init__(self, bus: EventBus) -> None:
        self.states: list[LaunchState] = []
        bus.subscribe(ClientLaunchStatus, self._on)

    async def _on(self, event: ClientLaunchStatus) -> None:
        self.states.append(event.state)


def connected() -> ClientConnected:
    return ClientConnected(ClientInfo(version="16.22.1.1", region="EUW", locale="es_ES"))


async def test_click_opens_league_through_the_riot_client(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    bus, spawned = EventBus(), []
    ClientLauncher(bus, installs_file=real_layout(tmp_path, exe), spawn=spawned.append)
    seen = Recorder(bus)
    await bus.publish(LaunchClientRequested())
    assert spawned == [exe] and seen.states == [LaunchState.STARTING]
    assert LAUNCH_ARGS == ("--launch-product=league_of_legends", "--launch-patchline=live")


async def test_repeated_clicks_while_opening_launch_once(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    bus, spawned = EventBus(), []
    ClientLauncher(bus, installs_file=real_layout(tmp_path, exe), spawn=spawned.append)
    for _ in range(3):
        await bus.publish(LaunchClientRequested())
    assert spawned == [exe]


async def test_nothing_is_launched_while_the_client_is_open(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    bus, spawned = EventBus(), []
    ClientLauncher(bus, installs_file=real_layout(tmp_path, exe), spawn=spawned.append)
    await bus.publish(connected())
    await bus.publish(LaunchClientRequested())
    assert spawned == []
    await bus.publish(ClientDisconnected())
    await bus.publish(LaunchClientRequested())
    assert spawned == [exe]


async def test_button_comes_back_if_the_client_never_connects(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    bus, spawned = EventBus(), []
    launcher = ClientLauncher(
        bus, installs_file=real_layout(tmp_path, exe), spawn=spawned.append,
        starting_timeout=0.05,
    )  # fmt: skip
    seen = Recorder(bus)
    await bus.publish(LaunchClientRequested())
    await asyncio.sleep(0.15)
    assert seen.states == [LaunchState.STARTING, LaunchState.IDLE] and not launcher.starting
    await bus.publish(LaunchClientRequested())  # se puede volver a intentar
    assert spawned == [exe, exe]


async def test_connecting_stops_the_wait(tmp_path: Path) -> None:
    exe = riot_install(tmp_path)
    bus = EventBus()
    launcher = ClientLauncher(
        bus, installs_file=real_layout(tmp_path, exe), spawn=lambda _exe: None,
        starting_timeout=0.05,
    )  # fmt: skip
    seen = Recorder(bus)
    await bus.publish(LaunchClientRequested())
    await bus.publish(connected())
    await asyncio.sleep(0.15)
    assert seen.states == [LaunchState.STARTING] and not launcher.starting


async def test_not_found_and_failed_are_reported(tmp_path: Path) -> None:
    bus = EventBus()
    ClientLauncher(bus, installs_file=tmp_path / "no-existe.json", spawn=lambda _exe: None)
    seen = Recorder(bus)
    await bus.publish(LaunchClientRequested())
    assert seen.states == [LaunchState.NOT_FOUND]

    def refuse(_exe: Path) -> None:
        raise PermissionError("acceso denegado")

    exe = riot_install(tmp_path)
    bus = EventBus()
    ClientLauncher(bus, installs_file=real_layout(tmp_path, exe), spawn=refuse)
    seen = Recorder(bus)
    await bus.publish(LaunchClientRequested())
    assert seen.states == [LaunchState.FAILED]


# --- Ventana ------------------------------------------------------------------------------


async def test_presenter_shows_the_launch_state_until_connected() -> None:
    bus = EventBus()
    presenter = UiPresenter(bus, loaded_gamedata())
    requests: list[LaunchClientRequested] = []

    async def record(event: LaunchClientRequested) -> None:
        requests.append(event)

    bus.subscribe(LaunchClientRequested, record)
    assert presenter.state["launch"] == "idle"
    assert await presenter.request_launch() is True and len(requests) == 1
    await bus.publish(ClientLaunchStatus(LaunchState.STARTING))
    assert presenter.state["launch"] == "starting"
    await bus.publish(connected())
    assert presenter.state["launch"] == "idle"
    assert await presenter.request_launch() is False and len(requests) == 1  # ya está abierto
