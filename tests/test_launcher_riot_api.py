"""Cliente de Riot ya abierto: su acceso directo solo lo pone delante y no abre el LoL.

Por eso, en cuanto tiene tu sesión iniciada, se le pide el LoL por su API local (lo mismo
que su botón «Jugar»). Aquí, con un cliente de Riot simulado (HTTPS con certificado de
prueba).
"""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import trustme
from aiohttp import web

from lolcoach.bus import EventBus
from lolcoach.events import ClientConnected, LaunchClientRequested
from lolcoach.lcu import client
from lolcoach.lcu.launcher import LAUNCH_PATH, ClientLauncher, read_riot_lockfile
from lolcoach.lcu.tls import create_ssl_context
from lolcoach.models import ClientInfo


class FakeRiotClient:
    """La API local del cliente de Riot, con lo justo: sesión, sesiones y lanzar."""

    def __init__(self) -> None:
        self.signed_in = False
        self.background = False  # en segundo plano casi todo responde 404
        self.sessions: dict[str, dict[str, Any]] = {"host": {"productId": "riot_client"}}
        self.launches = 0
        self.slow_launch = False
        self.auth_headers: set[str] = set()
        self.port = 0
        self._runner: web.AppRunner | None = None

    async def start(self, ssl_context: ssl.SSLContext) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=ssl_context)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        self.auth_headers.add(request.headers.get("Authorization", ""))
        not_found = web.json_response({"message": "Invalid URI format"}, status=404)
        if self.background:
            return not_found
        route = (request.method, request.path)
        if route == ("GET", "/product-session/v1/sessions"):
            return web.json_response(self.sessions)
        if route == ("GET", "/rso-auth/v1/authorization"):
            if self.signed_in:
                return web.json_response({"currentPlatform": "windows"})
            return web.json_response({"message": "No RSO Authorization"}, status=404)
        if route == ("POST", LAUNCH_PATH):
            if not self.signed_in:
                return web.json_response({"message": "Unauthorized"}, status=401)
            self.launches += 1
            if self.slow_launch:  # como el real: no contesta hasta haber lanzado el LoL
                await asyncio.sleep(1.0)
                return web.json_response("session-id")
            self.sessions["lol"] = {"productId": "league_of_legends"}
            return web.json_response("session-id")
        return not_found


@pytest.fixture
async def riot(ca: trustme.CA) -> AsyncIterator[FakeRiotClient]:
    server_ssl = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert("127.0.0.1", "rclient").configure_cert(server_ssl)
    server = FakeRiotClient()
    await server.start(server_ssl)
    yield server
    await server.stop()


def riot_lockfile(tmp_path: Path, port: int) -> Path:
    path = tmp_path / "Riot Client" / "Config" / "lockfile"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"Riot Client:1234:{port}:clave-secreta:https", encoding="utf-8")
    return path


def launcher_for(tmp_path: Path, port: int, ssl_context: ssl.SSLContext) -> ClientLauncher:
    exe = tmp_path / "Riot Client" / "RiotClientServices.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    installs = tmp_path / "RiotClientInstalls.json"
    installs.write_text(json.dumps({"rc_live": exe.as_posix()}), encoding="utf-8")
    return ClientLauncher(
        EventBus(), installs_file=installs, spawn=lambda _exe: None,
        riot_lockfile=riot_lockfile(tmp_path, port), ssl_context=ssl_context,
        starting_timeout=3.0, poll_interval=0.02,
    )  # fmt: skip


async def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        assert loop.time() < end, "no ha pasado a tiempo"
        await asyncio.sleep(0.01)


def connected() -> ClientConnected:
    return ClientConnected(ClientInfo(version="16.22.1.1", region="EUW", locale="es_ES"))


async def test_riot_client_already_open_is_asked_to_launch_league(
    tmp_path: Path, riot: FakeRiotClient, client_ssl: ssl.SSLContext
) -> None:
    riot.signed_in = True
    launcher = launcher_for(tmp_path, riot.port, client_ssl)
    await launcher._bus.publish(LaunchClientRequested())
    await wait_for(lambda: riot.launches == 1)
    await asyncio.sleep(0.1)
    assert riot.launches == 1 and launcher.starting  # una sola vez; espera a que conecte
    assert riot.auth_headers == {"Basic cmlvdDpjbGF2ZS1zZWNyZXRh"}  # riot:clave-secreta
    await launcher._bus.publish(connected())
    assert not launcher.starting


async def test_waits_for_you_to_sign_in_before_asking(
    tmp_path: Path, riot: FakeRiotClient, client_ssl: ssl.SSLContext
) -> None:
    riot.background = True  # al principio, aún en segundo plano
    launcher = launcher_for(tmp_path, riot.port, client_ssl)
    await launcher._bus.publish(LaunchClientRequested())
    await asyncio.sleep(0.1)
    riot.background = False  # se pone delante y pide iniciar sesión
    await asyncio.sleep(0.1)
    assert riot.launches == 0
    riot.signed_in = True  # has iniciado sesión
    await wait_for(lambda: riot.launches == 1)
    await launcher._bus.publish(connected())


async def test_slow_answer_to_launch_is_not_repeated(
    tmp_path: Path,
    riot: FakeRiotClient,
    client_ssl: ssl.SSLContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visto en real: el «abrir» tarda más que nuestro límite, pero ya ha funcionado."""
    monkeypatch.setattr(client, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=0.2))
    riot.signed_in = True
    riot.slow_launch = True
    launcher = launcher_for(tmp_path, riot.port, client_ssl)
    await launcher._bus.publish(LaunchClientRequested())
    await wait_for(lambda: riot.launches == 1)
    await asyncio.sleep(0.6)
    assert riot.launches == 1
    await launcher._bus.publish(connected())


async def test_not_asked_when_riot_already_launches_league(
    tmp_path: Path, riot: FakeRiotClient, client_ssl: ssl.SSLContext
) -> None:
    riot.signed_in = True
    riot.sessions["lol"] = {"productId": "league_of_legends"}  # lo abrió él solo
    launcher = launcher_for(tmp_path, riot.port, client_ssl)
    await launcher._bus.publish(LaunchClientRequested())
    await asyncio.sleep(0.2)
    assert riot.launches == 0
    await launcher._bus.publish(connected())


async def test_impostor_with_another_certificate_gets_nothing(
    tmp_path: Path, riot: FakeRiotClient
) -> None:
    """Si otro programa se hiciera pasar por el cliente de Riot, no se le envía la clave."""
    riot.signed_in = True
    other_ca = create_ssl_context(trustme.CA().cert_pem.bytes().decode("ascii"))
    launcher = launcher_for(tmp_path, riot.port, other_ca)
    await launcher._bus.publish(LaunchClientRequested())
    await asyncio.sleep(0.2)
    assert riot.launches == 0 and riot.auth_headers == set()
    await launcher._bus.publish(connected())


def test_riot_lockfile(tmp_path: Path) -> None:
    creds = read_riot_lockfile(riot_lockfile(tmp_path, 52644))
    assert creds is not None and creds.port == 52644 and creds.password == "clave-secreta"
    assert "clave-secreta" not in repr(creds)
    assert read_riot_lockfile(tmp_path / "no-existe") is None
    broken = tmp_path / "roto"
    broken.write_text("Riot Client:1:2", encoding="utf-8")
    assert read_riot_lockfile(broken) is None
