"""Fase 2 de punta a punta: qué se escribe exactamente en el cliente (simulado)."""

from __future__ import annotations

import asyncio
import copy
import ssl
from pathlib import Path
from typing import Any

from lolcoach.config import Settings
from lolcoach.events import LoadoutApplied, LoadoutReady
from lolcoach.lcu import endpoints
from lolcoach.models import GameflowPhase

from .factories import AHRI, JINX, blind_session
from .fake_lcu import FakeLcu, rune_page
from .harness import Harness, credentials_for, play_draft, wait_until

# Recomendación de Riot para Ahri (medio) con el fragmento de Vida por tener a Zed delante
AHRI_VS_ZED = [8112, 8139, 8140, 8106, 8226, 8210, 5005, 5008, 5011]
PAGES = "/lol-perks/v1/pages"


def our_page(fake: FakeLcu) -> dict[str, Any]:
    return next(p for p in fake.pages if p["name"].startswith("LoL Coach"))


async def test_runes_go_to_a_new_page_and_become_active(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    originals = copy.deepcopy(fake.pages)
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)

    page = our_page(fake)
    assert page["name"] == "LoL Coach: Ahri"
    assert page["selectedPerkIds"] == AHRI_VS_ZED
    assert (page["primaryStyleId"], page["subStyleId"]) == (8100, 8200)
    assert fake.current_page_id == page["id"]
    assert fake.pages[:2] == originals  # tus páginas, intactas

    applied = app.of(LoadoutApplied)[0]
    assert applied.runes_applied and applied.spells_applied and applied.problems == ()
    assert applied.page_name == "LoL Coach: Ahri"
    # Ya tenías Destello + Prender: no hace falta tocar los hechizos
    assert fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION) == []


async def test_spells_are_set_keeping_flash_on_your_key(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app, my_spells=(12, 4))  # Teleportar + Destello en la F
    patches = fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION)
    assert patches == [
        ("PATCH", endpoints.CHAMP_SELECT_MY_SELECTION, {"spell1Id": 14, "spell2Id": 4})
    ]


async def test_full_pages_reuse_only_the_empty_one(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    fake.pages.append(rune_page(13, "", valid=False))
    originals = copy.deepcopy(fake.pages[:2])
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)

    assert [w[:2] for w in fake.writes_to(PAGES)] == [("PUT", f"{PAGES}/13")]
    reused = fake.page(13)
    assert reused is not None and reused["name"] == "LoL Coach: Ahri" and reused["isValid"]
    assert fake.pages[:2] == originals
    assert fake.current_page_id == 13


async def test_full_pages_without_empty_one_only_recommend(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    fake.pages.append(rune_page(13, "Mi página buena"))
    originals = copy.deepcopy(fake.pages)
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app, my_spells=(4, 12))

    assert fake.pages == originals and fake.current_page_id == 11
    assert fake.writes_to("/lol-perks") == []
    applied = app.of(LoadoutApplied)[0]
    assert not applied.runes_applied and applied.page_name is None
    assert any("No hay hueco" in p for p in applied.problems)
    assert applied.spells_applied  # los hechizos sí se ponen
    assert app.of(LoadoutReady)[0].loadout.runes is not None  # y se muestran las runas


async def test_existing_coach_page_is_reused(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    fake.pages.append(rune_page(20, "LoL Coach: Zed"))
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)
    assert [w[:2] for w in fake.writes_to(PAGES)] == [("PUT", f"{PAGES}/20")]
    assert len(fake.pages) == 3 and our_page(fake)["name"] == "LoL Coach: Ahri"


async def test_manual_changes_are_respected(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        state = await play_draft(fake, app)
        writes = len(fake.writes)
        # Tú eliges otra página y otros hechizos a mano
        fake.current_page_id = 11
        state["myTeam"][2]["spell2Id"] = 12
        await fake.set_session(state)
        await asyncio.sleep(0.3)
    assert len(fake.writes) == writes  # la app no vuelve a tocar nada
    assert fake.current_page_id == 11
    assert len(app.of(LoadoutApplied)) == 1


async def test_champion_trade_after_last_pick_reapplies(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        state = await play_draft(fake, app)
        state["myTeam"][2]["championId"], state["myTeam"][3]["championId"] = JINX, AHRI
        await fake.set_session(state)
        await wait_until(lambda: len(app.of(LoadoutApplied)) == 2)
    assert [a.loadout.champion_id for a in app.of(LoadoutApplied)] == [AHRI, JINX]
    assert our_page(fake)["name"] == "LoL Coach: Jinx"
    assert sum(p["name"].startswith("LoL Coach") for p in fake.pages) == 1  # una sola página


async def test_client_temporary_page_is_replaced(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        # Justo después de aplicar, el cliente activa su página temporal recomendada
        app.bus.subscribe(LoadoutApplied, lambda _e: fake.add_temporary_page())
        await play_draft(fake, app)
        page_id = our_page(fake)["id"]
        await wait_until(lambda: fake.current_page_id == page_id)
    assert len(fake.writes_to(endpoints.PERK_CURRENT_PAGE)) == 2


async def test_disabled_in_settings_only_recommends(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    settings = Settings(
        data_dir=Path("."), auto_runes=False, auto_spells=False, auto_item_set=False
    )
    async with Harness(credentials_for(fake), client_ssl, settings=settings) as app:
        await play_draft(fake, app, my_spells=(4, 12))
    assert fake.writes == []
    applied = app.of(LoadoutApplied)[0]
    assert not applied.runes_applied and not applied.spells_applied
    assert app.of(LoadoutReady)


async def test_blind_pick_applies_champion_defaults(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
        await fake.set_phase("ChampSelect")
        await wait_until(lambda: endpoints.CHAMP_SELECT_SESSION in fake.requests)
        await fake.set_session(blind_session(my_locked=True))
        await wait_until(lambda: bool(app.of(LoadoutApplied)))
    loadout = app.of(LoadoutReady)[0].loadout
    assert loadout.champion_id == AHRI and loadout.adjustments == ()
    assert loadout.base is not None and our_page(fake)["selectedPerkIds"] == list(
        loadout.base.page.perk_ids
    )
    assert fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION)[0][2] == {
        "spell1Id": 4,
        "spell2Id": 14,
    }
