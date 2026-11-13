"""Ventana: presentador (estado) y servidor local (seguridad y tiempo real)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest

from lolcoach.builds.recommender import BuildRecommender
from lolcoach.bus import EventBus
from lolcoach.events import (
    BuildReady,
    ClientDisconnected,
    GameflowPhaseChanged,
    LiveGameEnded,
    LiveGameUpdated,
)
from lolcoach.ingame.advisor import advise
from lolcoach.ingame.analysis import analyze
from lolcoach.ingame.models import parse_all_game_data
from lolcoach.models import GameflowPhase, Position
from lolcoach.ui.web.presenter import UiPresenter
from lolcoach.ui.web.server import UiServer

from .gamedata_fixture import FixtureClient, loaded_gamedata
from .live_factories import standard_game


async def live_event(*, fed_zed: bool = True) -> LiveGameUpdated:
    gd = loaded_gamedata()
    plan = await BuildRecommender(gd).recommend_for(
        FixtureClient(), 103, Position.MIDDLE, [122, 104, 238, 145, 16], 11
    )
    assert plan is not None
    data = (
        standard_game(zed_kda=(8, 1, 2), zed_items=(1055, 3158, 3142, 6697))
        if fed_zed
        else standard_game()
    )
    game = parse_all_game_data(data)
    report = analyze(
        game,
        champion_id=gd.champion_id_by_alias,
        item_price=lambda i: item.price_total if (item := gd.item(i)) else 0,
        profiles=gd._profiles,
        positions_for=gd.positions_for,
        my_position=Position.MIDDLE,
    )
    assert game.me is not None
    advice = advise(plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name)
    return LiveGameUpdated(game, report, advice, plan)


# --- Presentador -------------------------------------------------------------------


async def test_presenter_builds_the_game_state() -> None:
    bus = EventBus()
    presenter = UiPresenter(bus, loaded_gamedata())
    seen: list[dict] = []

    async def listener(state: dict) -> None:
        seen.append(state)

    presenter.add_listener(listener)
    await bus.publish(GameflowPhaseChanged(GameflowPhase.LOBBY, GameflowPhase.IN_PROGRESS))
    await bus.publish(await live_event())
    game = presenter.state["game"]
    assert presenter.state["phaseLabel"] == "En partida"
    assert game["time"] == "15:00" and game["gold"] == 500
    assert game["next"]["id"] == 3157 and "Zed" in game["next"]["reason"]
    zed = game["enemies"][0]
    assert zed["name"] == "Zed" and zed["strength"] == "very_strong" and zed["lane"]
    assert zed["damage"] == "AD" and zed["strengthLabel"] == "Muy fuerte"
    assert len(game["buildProgress"]) == 6 and game["buildProgress"][0]["owned"]
    assert presenter.state["build"] is not None  # se rellena aunque no hubiera selección
    assert len(seen) == 2

    await bus.publish(LiveGameEnded())
    assert presenter.state["game"] is None
    await bus.publish(ClientDisconnected())
    assert not presenter.state["connected"]


async def test_presenter_champ_select_build() -> None:
    bus = EventBus()
    gd = loaded_gamedata()
    presenter = UiPresenter(bus, gd)
    plan = await BuildRecommender(gd).recommend_for(FixtureClient(), 103, Position.MIDDLE, [], 11)
    assert plan is not None
    await bus.publish(BuildReady(plan))
    build = presenter.state["build"]
    assert build["archetype"] == "mago de daño explosivo"
    assert build["core"][0]["name"] == "Eco de Luden"
    assert presenter.state["champion"]["name"] == "Ahri"


# --- Servidor ------------------------------------------------------------------------


@pytest.fixture
async def server() -> AsyncIterator[tuple[UiServer, EventBus]]:
    bus = EventBus()
    presenter = UiPresenter(bus, loaded_gamedata())
    ui = UiServer(presenter, loaded_gamedata(), connector=None)
    await ui.start()
    yield ui, bus
    await ui.stop()


async def test_page_requires_the_key(server: tuple[UiServer, EventBus]) -> None:
    ui, _ = server
    base = f"http://127.0.0.1:{ui.port}"
    async with aiohttp.ClientSession() as http:
        assert (await http.get(base + "/")).status == 403
        assert (await http.get(base + "/?t=mala")).status == 403
        ok = await http.get(ui.url)
        assert ok.status == 200 and "LoL Coach" in await ok.text()
        csp = ok.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert ok.headers["X-Frame-Options"] == "DENY"
        assert (await http.get(base + "/static/app.js")).status == 200
        assert (await http.get(base + "/static/secreto.txt")).status == 404


async def test_foreign_host_is_rejected(server: tuple[UiServer, EventBus]) -> None:
    ui, _ = server
    async with aiohttp.ClientSession() as http:
        response = await http.get(ui.url, headers={"Host": f"evil.example:{ui.port}"})
        assert response.status == 403


async def test_websocket_checks_origin_and_key(server: tuple[UiServer, EventBus]) -> None:
    ui, _ = server
    ws_url = f"http://127.0.0.1:{ui.port}/ws?t={ui.token}"
    async with aiohttp.ClientSession() as http:
        for origin in ("https://evil.example", None):
            headers = {"Origin": origin} if origin else {}
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await http.ws_connect(ws_url, headers=headers)
        with pytest.raises(aiohttp.WSServerHandshakeError):
            await http.ws_connect(
                f"http://127.0.0.1:{ui.port}/ws?t=mala",
                headers={"Origin": f"http://127.0.0.1:{ui.port}"},
            )


async def test_websocket_pushes_state_changes(server: tuple[UiServer, EventBus]) -> None:
    ui, bus = server
    async with (
        aiohttp.ClientSession() as http,
        http.ws_connect(
            f"http://127.0.0.1:{ui.port}/ws?t={ui.token}",
            headers={"Origin": f"http://127.0.0.1:{ui.port}"},
        ) as ws,
    ):
        first = await ws.receive_json(timeout=5)
        assert first["type"] == "state" and first["state"]["game"] is None
        await bus.publish(await live_event())
        update = await ws.receive_json(timeout=5)
        assert update["state"]["game"]["next"]["id"] == 3157


async def test_icons_only_by_numeric_id(server: tuple[UiServer, EventBus]) -> None:
    ui, _ = server
    base = f"http://127.0.0.1:{ui.port}"
    async with aiohttp.ClientSession() as http:
        assert (await http.get(base + "/icon/item/abc.png")).status == 404
        assert (await http.get(base + "/icon/item/..%2Fsecret.png")).status == 404
        # Sin cliente de LoL conectado no hay iconos (pero tampoco errores)
        assert (await http.get(base + "/icon/item/3157.png")).status == 404
