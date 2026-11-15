"""Ventana: presentador (estado) y servidor local (seguridad y tiempo real)."""

from __future__ import annotations

import dataclasses
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
from lolcoach.ingame.models import LivePlayer, parse_all_game_data
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
        # Con el núcleo hecho (Hechicero, Luden, Llamasombría, Rabadon) toca responder a Zed
        standard_game(
            zed_kda=(8, 1, 2),
            zed_items=(1055, 3158, 3142, 6697),
            my_items=(3020, 6655, 4645, 3089),
        )
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
    advice = advise(
        plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name,
        game_time=game.game_time,
    )  # fmt: skip
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
    zed = next(p for p in game["enemies"] if p["name"] == "Zed")
    assert zed["name"] == "Zed" and zed["strength"] == "very_strong" and zed["lane"]
    assert zed["damage"] == "AD" and zed["strengthLabel"] == "Muy fuerte"
    assert len(game["buildProgress"]) == 6 and game["buildProgress"][0]["owned"]
    assert presenter.state["build"] is not None  # se rellena aunque no hubiera selección
    assert len(seen) == 2

    await bus.publish(LiveGameEnded())
    assert presenter.state["game"] is None
    await bus.publish(ClientDisconnected())
    assert not presenter.state["connected"]


async def test_players_are_listed_by_lane_on_both_teams() -> None:
    """Fila a fila, cada uno frente a su rival de línea: superior, jungla, medio, inferior, apoyo
    (aunque Zed sea el más peligroso, sigue en medio; tú vas en tu línea, no el primero)."""
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    await presenter._on_live(await live_event())
    game = presenter.state["game"]
    lanes = ["Superior", "Jungla", "Medio", "Inferior", "Soporte"]
    assert [p["name"] for p in game["enemies"]] == ["Darius", "Graves", "Zed", "Kai'Sa", "Soraka"]
    assert [p["position"] for p in game["enemies"]] == lanes
    team = game["team"]
    assert [p["name"] for p in team] == ["Garen", "Lee Sin", "Ahri", "Jinx", "Thresh"]
    assert [p["position"] for p in team] == lanes
    assert [p["me"] for p in team] == [False, False, True, False, False]


async def test_players_without_a_lane_go_last_in_game_order() -> None:
    """Sin línea conocida (algunos modos): al final, en el orden en que los da el juego."""
    event = await live_event()
    unknown = {"Graves", "Darius"}

    def without_lane(player: LivePlayer) -> LivePlayer:
        if player.champion_alias in unknown:
            return dataclasses.replace(player, position=Position.NONE)
        return player

    game = dataclasses.replace(event.game, enemies=tuple(map(without_lane, event.game.enemies)))
    enemies = tuple(
        dataclasses.replace(
            r,
            player=without_lane(r.player),
            position=Position.NONE if r.player.champion_alias in unknown else r.position,
        )
        for r in event.report.enemies
    )
    report = dataclasses.replace(event.report, enemies=enemies)
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    await presenter._on_live(dataclasses.replace(event, game=game, report=report))
    names = [p["name"] for p in presenter.state["game"]["enemies"]]
    assert names == ["Zed", "Kai'Sa", "Soraka", "Darius", "Graves"]


async def test_build_card_follows_a_plan_recomputed_in_game() -> None:
    """App abierta a mitad de partida: la build se recalcula al llegar las estadísticas, y
    la tarjeta de build (la que queda al acabar la partida) debe mostrar la nueva."""
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    event = await live_event()
    await presenter._on_live(event)
    assert event.plan is not None
    newer = dataclasses.replace(event.plan, source="estadísticas de 40 partidas")
    await presenter._on_live(dataclasses.replace(event, plan=newer))
    assert presenter.state["build"]["source"] == "estadísticas de 40 partidas"


async def test_live_updates_keep_the_champ_select_build() -> None:
    gd = loaded_gamedata()
    presenter = UiPresenter(EventBus(), gd)
    event = await live_event()
    assert event.plan is not None
    await presenter._on_build(BuildReady(event.plan))
    shown = presenter.state["build"]
    await presenter._on_live(event)  # el mismo plan: no se repinta la tarjeta
    assert presenter.state["build"] is shown


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
        assert ok.status == 200 and "Erizo Coach" in await ok.text()
        csp = ok.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert ok.headers["X-Frame-Options"] == "DENY"
        assert (await http.get(base + "/static/app.js")).status == 200
        logo = await http.get(base + "/static/logo.png")
        assert logo.status == 200 and logo.content_type == "image/png"
        assert (await logo.read())[1:4] == b"PNG"
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


async def test_starting_items_are_shown_together() -> None:
    gd = loaded_gamedata()
    bus = EventBus()
    presenter = UiPresenter(bus, gd)
    plan = await BuildRecommender(gd).recommend_for(
        FixtureClient(), 103, Position.MIDDLE, [122, 104, 238, 145, 16], 11
    )
    assert plan is not None
    game = parse_all_game_data(standard_game(my_items=(), gold=500, game_time=3))
    report = analyze(
        game,
        champion_id=gd.champion_id_by_alias,
        item_price=lambda i: item.price_total if (item := gd.item(i)) else 0,
        profiles=gd._profiles,
        positions_for=gd.positions_for,
        my_position=Position.MIDDLE,
    )
    assert game.me is not None
    advice = advise(
        plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name,
        game_time=game.game_time,
    )  # fmt: skip
    await bus.publish(LiveGameUpdated(game, report, advice, plan))
    shown = presenter.state["game"]["next"]
    assert [s["id"] for s in shown["starting"]] == [s.item_id for s in plan.starting]
    assert shown["price"] == sum(s["price"] * s["count"] for s in shown["starting"])
    assert " + " in shown["name"] or len(plan.starting) == 1


async def test_lanes_pair_each_ally_with_their_rival_and_the_gold_gap() -> None:
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    await presenter._on_live(await live_event())
    rows = presenter.state["game"]["matchups"]
    assert [(r["ally"]["name"], r["enemy"]["name"]) for r in rows] == [
        ("Garen", "Darius"), ("Lee Sin", "Graves"), ("Ahri", "Zed"),
        ("Jinx", "Kai'Sa"), ("Thresh", "Soraka"),
    ]  # fmt: skip
    ahri = rows[2]
    assert ahri["ally"]["me"] and ahri["position"] == "Medio"
    assert ahri["goldDiff"] == ahri["ally"]["laneGoldDiff"] == -ahri["enemy"]["laneGoldDiff"]
    # Oro total de cada equipo, como en la retransmisión
    team = presenter.state["game"]["teamGold"]
    assert team["mine"] == sum(r["ally"]["totalGold"] for r in rows)
    assert team["theirs"] == sum(r["enemy"]["totalGold"] for r in rows)


async def test_without_lanes_there_are_no_matchups() -> None:
    event = await live_event()
    report = dataclasses.replace(
        event.report,
        me=dataclasses.replace(event.report.me, lane_rival_id=0, gold_vs_lane_rival=None),  # type: ignore[arg-type]
        allies=tuple(
            dataclasses.replace(r, lane_rival_id=0, gold_vs_lane_rival=None)
            for r in event.report.allies
        ),
    )
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    await presenter._on_live(dataclasses.replace(event, report=report))
    assert presenter.state["game"]["matchups"] == []  # la ventana pinta las dos listas
