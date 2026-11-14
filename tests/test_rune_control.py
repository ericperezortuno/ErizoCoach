"""Runas en la ventana: se ven con sus imágenes, se cambian desde ella y se ponen solas si
mantienes la preselección 5 segundos."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any

import aiohttp

from lolcoach.bus import EventBus
from lolcoach.events import (
    ChampSelectStarted,
    LoadoutApplied,
    LoadoutInClient,
    LoadoutPreview,
    RunesBy,
    RunesEditRequested,
)
from lolcoach.gamedata.runes import RunePage
from lolcoach.lcu import endpoints
from lolcoach.models import GameflowPhase
from lolcoach.ui.web.icons import IconProvider
from lolcoach.ui.web.presenter import UiPresenter
from lolcoach.ui.web.server import UiServer

from .factories import AHRI, DraftScenario
from .fake_lcu import FakeLcu
from .gamedata_fixture import loaded_gamedata
from .harness import Harness, credentials_for, wait_until

PAGES = "/lol-perks/v1/pages"
HOLD = 0.25  # en las pruebas, «5 segundos» son esto
AHRI_VS_ZED = [8112, 8139, 8140, 8106, 8226, 8210, 5005, 5008, 5011]


def our_page(fake: FakeLcu) -> dict[str, Any]:
    return next(p for p in fake.pages if p["name"].startswith("LoL Coach"))


def page_writes(fake: FakeLcu) -> list[tuple[str, str, Any]]:
    return [w for w in fake.writes_to(PAGES) if w[0] in ("POST", "PUT")]


def edited_page() -> RunePage:
    """La de Ahri contra Zed con otra piedra angular de Dominación."""
    domination = loaded_gamedata().styles[8100]
    keystone = next(k for k in domination.rows[0] if k != AHRI_VS_ZED[0])
    return RunePage(8100, 8200, (keystone, *AHRI_VS_ZED[1:]))


async def start_hovering(fake: FakeLcu, app: Harness, *, spells: tuple[int, int] = (12, 4)):
    """Selección en draft: aún no te toca y preseleccionas a Ahri (con Teleportar + Destello)."""
    await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
    await fake.set_phase("ChampSelect")
    await wait_until(lambda: endpoints.CHAMP_SELECT_SESSION in fake.requests)
    scenario = DraftScenario()
    state = scenario.state(3, hover=AHRI)
    state["myTeam"][2]["spell1Id"], state["myTeam"][2]["spell2Id"] = spells
    await fake.set_session(state)
    await wait_until(lambda: any(e.champion_id == AHRI for e in app.of(LoadoutPreview)))
    return scenario


async def finish_draft(fake: FakeLcu, app: Harness, scenario: DraftScenario) -> None:
    """Se completan los picks conservando los hechizos que haya ahora en la sesión."""
    assert fake.session is not None
    me = next(p for p in fake.session["myTeam"] if p["cellId"] == 2)
    spells = me["spell1Id"], me["spell2Id"]
    for picks in range(4, 11):
        state = scenario.state(picks)
        state["myTeam"][2]["spell1Id"], state["myTeam"][2]["spell2Id"] = spells
        await fake.set_session(state)
    await wait_until(lambda: any(not a.provisional for a in app.of(LoadoutApplied)))


# --- Preselección mantenida ------------------------------------------------------------


async def test_holding_a_pre_pick_applies_runes_and_spells(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl, hover_apply_delay=HOLD) as app:
        await start_hovering(fake, app)
        assert page_writes(fake) == []  # nada más preseleccionar: solo previsión
        await wait_until(lambda: bool(app.of(LoadoutApplied)))
        applied = app.of(LoadoutApplied)[0]
        assert applied.provisional and applied.runes_applied and applied.spells_applied
        page = our_page(fake)
        assert page["name"] == "LoL Coach: Ahri" and fake.current_page_id == page["id"]
        me = next(p for p in fake.session["myTeam"] if p["cellId"] == 2)  # type: ignore[index]
        assert (me["spell1Id"], me["spell2Id"]) == (14, 4)  # Prender, Destello en tu tecla
        shown = app.of(LoadoutInClient)[-1]
        assert shown.champion_id == AHRI and shown.runes_by == RunesBy.PREVIEW
        assert shown.spells == (14, 4) and shown.spells_by == RunesBy.PREVIEW


async def test_a_short_hover_applies_nothing(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    async with Harness(credentials_for(fake), client_ssl, hover_apply_delay=HOLD) as app:
        scenario = await start_hovering(fake, app)
        await fake.set_session(scenario.state(3))  # deja de mirar a Ahri enseguida
        await asyncio.sleep(HOLD * 2)
        assert page_writes(fake) == []
        assert fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION) == []
        assert app.of(LoadoutApplied) == []


async def test_the_last_pick_adjusts_the_provisional_runes(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl, hover_apply_delay=HOLD) as app:
        scenario = await start_hovering(fake, app)
        await wait_until(lambda: bool(app.of(LoadoutApplied)))
        await finish_draft(fake, app, scenario)
    final = [a for a in app.of(LoadoutApplied) if not a.provisional]
    assert len(final) == 1 and final[0].runes_applied
    assert our_page(fake)["selectedPerkIds"] == AHRI_VS_ZED  # con Zed ya conocido
    assert sum(p["name"].startswith("LoL Coach") for p in fake.pages) == 1
    assert app.of(LoadoutInClient)[-1].runes_by == RunesBy.COACH


async def test_runes_you_change_in_the_client_are_kept(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl, hover_apply_delay=HOLD) as app:
        scenario = await start_hovering(fake, app)
        await wait_until(lambda: bool(app.of(LoadoutApplied)))
        mine = list(edited_page().perk_ids)
        our_page(fake)["selectedPerkIds"] = mine  # la cambias tú en el cliente
        writes = len(page_writes(fake))
        await finish_draft(fake, app, scenario)
    assert len(page_writes(fake)) == writes  # no se ha vuelto a escribir
    assert our_page(fake)["selectedPerkIds"] == mine
    shown = app.of(LoadoutInClient)[-1]
    assert shown.runes_by == RunesBy.YOU_IN_CLIENT and shown.runes is not None
    assert list(shown.runes.perk_ids) == mine


async def test_spells_you_change_in_the_client_are_kept(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl, hover_apply_delay=HOLD) as app:
        scenario = await start_hovering(fake, app)
        await wait_until(lambda: bool(app.of(LoadoutApplied)))
        assert fake.session is not None
        fake.session["myTeam"][2]["spell1Id"] = 12  # vuelves a Teleportar a mano
        await fake.set_session(fake.session)
        await wait_until(
            lambda: (
                app.tracker.snapshot is not None
                and app.tracker.snapshot.me is not None
                and app.tracker.snapshot.me.spell1_id == 12
            )
        )
        patches = len(fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION))
        await finish_draft(fake, app, scenario)
    assert len(fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION)) == patches
    shown = app.of(LoadoutInClient)[-1]
    assert shown.spells == (12, 4) and shown.spells_by == RunesBy.YOU_IN_CLIENT


# --- Cambiar las runas desde la ventana ---------------------------------------------------


async def test_runes_edited_in_the_window_go_to_the_client(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        scenario = await start_hovering(fake, app)
        page = edited_page()
        await app.bus.publish(RunesEditRequested(page))
        assert our_page(fake)["selectedPerkIds"] == list(page.perk_ids)
        assert fake.current_page_id == our_page(fake)["id"]
        shown = app.of(LoadoutInClient)[-1]
        assert shown.runes == page and shown.runes_by == RunesBy.YOU_IN_APP
        await finish_draft(fake, app, scenario)
    # Al último pick no se pisan tus runas (los hechizos sí se ponen)
    assert our_page(fake)["selectedPerkIds"] == list(page.perk_ids)
    final = next(a for a in app.of(LoadoutApplied) if not a.provisional)
    assert not final.runes_applied and final.spells_applied


async def test_an_invalid_page_from_the_window_is_rejected(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await start_hovering(fake, app)
        wrong = RunePage(8100, 8200, (8005, *AHRI_VS_ZED[1:]))  # piedra de Precisión
        await app.bus.publish(RunesEditRequested(wrong))
        assert page_writes(fake) == []
        assert "no es válida" in app.of(LoadoutInClient)[-1].problem


async def test_runes_are_only_edited_in_champ_select(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
        await app.bus.publish(RunesEditRequested(edited_page()))
        assert page_writes(fake) == []
        assert "selección" in app.of(LoadoutInClient)[-1].problem


# --- Ventana ----------------------------------------------------------------------------------


async def test_the_window_shows_the_runes_in_your_client() -> None:
    from lolcoach.champselect.parser import parse_session
    from lolcoach.loadout.recommender import LoadoutRecommender

    from .gamedata_fixture import FixtureClient

    bus, gd = EventBus(), loaded_gamedata()
    presenter = UiPresenter(bus, gd)
    snapshot = parse_session(DraftScenario().state(3, hover=AHRI)).with_my_champion(AHRI)
    loadout = await LoadoutRecommender(gd).recommend(FixtureClient(), snapshot, None)
    assert loadout is not None and loadout.runes is not None
    await bus.publish(ChampSelectStarted(snapshot, None))
    await bus.publish(LoadoutPreview(AHRI, loadout))
    state = presenter.state["loadout"]
    assert state["preview"] and "5 segundos" in state["status"] and state["runesBy"] == ""
    assert state["page"]["keystone"]["id"] == loadout.runes.keystone_id
    assert [r["id"] for r in state["page"]["primary"]] == list(loadout.runes.perk_ids[1:4])
    assert len(state["page"]["shards"]) == 3 and len(state["spells"]) == 2
    catalog = presenter.state["runeCatalog"]
    assert {s["id"] for s in catalog["styles"]} == {8000, 8100, 8200, 8300, 8400}
    assert all(len(s["rows"]) == 4 for s in catalog["styles"]) and len(catalog["shards"]) == 3

    mine = edited_page()
    await bus.publish(
        LoadoutInClient(AHRI, mine, (4, 14), RunesBy.YOU_IN_APP, RunesBy.PREVIEW, "LoL Coach: Ahri")
    )
    state = presenter.state["loadout"]
    assert state["page"]["perkIds"] == list(mine.perk_ids) and state["runesBy"] == "you_in_app"
    assert "Tus cambios" in state["status"] and "LoL Coach: Ahri" in state["status"]
    # Lo puesto para otro campeón (el que mirabas antes) no se mezcla
    await bus.publish(LoadoutInClient(17, mine, (4, 14), RunesBy.PREVIEW, RunesBy.PREVIEW))
    assert presenter.state["loadout"]["page"]["keystone"]["id"] == loadout.runes.keystone_id


async def test_the_window_can_only_ask_for_a_rune_page() -> None:
    bus = EventBus()
    seen: list[RunesEditRequested] = []
    bus.subscribe(RunesEditRequested, seen.append)
    presenter = UiPresenter(bus, loaded_gamedata())
    good = {"primaryStyleId": 8100, "subStyleId": 8200, "perkIds": AHRI_VS_ZED}
    for bad in (
        None,
        "8100",
        {**good, "perkIds": AHRI_VS_ZED[:8]},
        {**good, "perkIds": [*AHRI_VS_ZED[:8], "5011"]},
        {**good, "perkIds": [*AHRI_VS_ZED[:8], True]},
        {**good, "primaryStyleId": -1},
        {**good, "subStyleId": 10**9},
    ):
        assert not await presenter.request_runes(bad)
    assert seen == []
    assert await presenter.request_runes(good)
    assert seen == [RunesEditRequested(RunePage(8100, 8200, tuple(AHRI_VS_ZED)))]


async def test_rune_changes_arrive_through_the_websocket() -> None:
    bus = EventBus()
    seen: list[RunesEditRequested] = []
    bus.subscribe(RunesEditRequested, seen.append)
    ui = UiServer(UiPresenter(bus, loaded_gamedata()), loaded_gamedata(), connector=None)
    await ui.start()
    try:
        async with (
            aiohttp.ClientSession() as http,
            http.ws_connect(
                f"http://127.0.0.1:{ui.port}/ws?t={ui.token}",
                headers={"Origin": f"http://127.0.0.1:{ui.port}"},
            ) as ws,
        ):
            await ws.receive_json(timeout=5)
            await ws.send_str("no es json")
            await ws.send_json({"type": "otra cosa", "page": {}})
            await ws.send_str("x" * 5000)
            page = {"primaryStyleId": 8100, "subStyleId": 8200, "perkIds": AHRI_VS_ZED}
            await ws.send_json({"type": "setRunes", "page": page})
            await wait_until(lambda: bool(seen))
        assert len(seen) == 1 and list(seen[0].page.perk_ids) == AHRI_VS_ZED
    finally:
        await ui.stop()


def test_rune_and_spell_icons_come_only_from_the_game() -> None:
    gd = loaded_gamedata()
    gd._set_icons(
        "perk",
        [
            {"id": 8112, "iconPath": "/lol-game-data/assets/v1/perk-images/Electrocute.png"},
            {"id": 8139, "iconPath": "https://evil.example/x.png"},
            {"id": 8140, "iconPath": "/lol-summoner/v1/current-summoner"},
        ],
    )
    gd._set_icons("spell", [{"id": 4, "iconPath": "/lol-game-data/assets/DATA/Flash.png"}])
    icons = IconProvider(gd, None)
    assert icons.path_for("perk", 8112) == "/lol-game-data/assets/v1/perk-images/Electrocute.png"
    assert icons.path_for("perk", 8139) is None and icons.path_for("perk", 8140) is None
    assert icons.path_for("spell", 4) == "/lol-game-data/assets/DATA/Flash.png"
    assert icons.path_for("perk", 4) is None  # cada tipo, por separado
    assert icons.path_for("evil", 8112) is None
