"""Fase 3 de punta a punta: la build llega a la tienda y tus conjuntos se conservan."""

from __future__ import annotations

import copy
import ssl
from pathlib import Path

from lolcoach.config import Settings
from lolcoach.events import BuildApplied, BuildReady
from lolcoach.lcu import endpoints

from .factories import AHRI, JINX
from .fake_lcu import FakeLcu, user_item_set
from .harness import Harness, credentials_for, play_draft, wait_until

SETS = "/lol-item-sets/v1/item-sets/"


def coach_sets(fake: FakeLcu) -> list[dict]:
    return [s for s in fake.item_sets if s["title"].startswith("Erizo Coach")]


async def test_build_goes_to_the_shop_and_your_sets_are_kept(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    yours = copy.deepcopy(fake.item_sets)
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)

    applied = app.of(BuildApplied)[0]
    assert applied.applied and applied.set_title == "Erizo Coach: Ahri" and applied.problems == ()
    (ours,) = coach_sets(fake)
    assert ours["associatedChampions"] == [AHRI] and ours["associatedMaps"] == [11]
    assert ours["blocks"][0]["type"] == "Inicio"
    assert [s for s in fake.item_sets if not s["title"].startswith("Erizo Coach")] == yours
    assert fake.item_sets[0]["title"].startswith("Erizo Coach")  # el nuestro, el primero


async def test_existing_coach_set_is_replaced_not_duplicated(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    old = user_item_set("coach-uid", "Erizo Coach: Zed")
    fake.item_sets.append(old)
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)
    (ours,) = coach_sets(fake)
    assert ours["uid"] == "coach-uid" and ours["title"] == "Erizo Coach: Ahri"
    assert len(fake.item_sets) == 2


async def test_set_with_the_old_name_is_replaced(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    """La app se llamaba «LoL Coach»: su conjunto se sustituye, no se queda otro viejo."""
    fake.item_sets.append(user_item_set("old-uid", "LoL Coach: Zed"))
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)
    (ours,) = coach_sets(fake)
    assert ours["uid"] == "old-uid" and ours["title"] == "Erizo Coach: Ahri"
    assert not any(s["title"].startswith("LoL Coach") for s in fake.item_sets)
    assert len(fake.item_sets) == 2


async def test_trade_after_last_pick_rewrites_the_set(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        state = await play_draft(fake, app)
        state["myTeam"][2]["championId"], state["myTeam"][3]["championId"] = JINX, AHRI
        await fake.set_session(state)
        await wait_until(lambda: len(app.of(BuildApplied)) == 2)
    (ours,) = coach_sets(fake)
    assert ours["title"] == "Erizo Coach: Jinx" and ours["associatedChampions"] == [JINX]
    assert app.builds.current_plan is not None and app.builds.current_plan.champion_id == JINX


async def test_client_losing_your_sets_is_detected_and_restored(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    yours = copy.deepcopy(fake.item_sets)
    fake.drop_other_item_sets = True  # el cliente "pierde" tus conjuntos al guardar
    async with Harness(credentials_for(fake), client_ssl) as app:
        await play_draft(fake, app)
    applied = app.of(BuildApplied)[0]
    assert not applied.applied and applied.problems
    assert fake.item_sets == yours  # restaurado tal cual estaba


async def test_disabled_in_settings_only_recommends(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    settings = Settings(data_dir=Path("."), auto_item_set=False)
    async with Harness(credentials_for(fake), client_ssl, settings=settings) as app:
        await play_draft(fake, app)
    assert fake.writes_to(SETS) == []
    assert app.of(BuildReady) and not app.of(BuildApplied)[0].applied


async def test_summoner_is_read_once_per_connection(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        state = await play_draft(fake, app)
        state["myTeam"][2]["championId"], state["myTeam"][3]["championId"] = JINX, AHRI
        await fake.set_session(state)
        await wait_until(lambda: len(app.of(BuildApplied)) == 2)
    assert fake.requests.count(endpoints.CURRENT_SUMMONER) == 1
