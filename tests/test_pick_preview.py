"""Previsión: runas y hechizos del campeón que preseleccionas, antes del último pick."""

from __future__ import annotations

import ssl

from erizocoach.bus import EventBus
from erizocoach.champselect.parser import parse_session
from erizocoach.champselect.tracker import ChampSelectTracker
from erizocoach.events import (
    ChampSelectStarted,
    LoadoutApplied,
    LoadoutPreview,
    LoadoutReady,
    MyPickPreviewChanged,
)
from erizocoach.lcu import endpoints
from erizocoach.loadout.recommender import LoadoutRecommender
from erizocoach.models import GameflowPhase
from erizocoach.ui.web.presenter import UiPresenter

from .factories import AHRI, YASUO, ZED, DraftScenario, action, player, session
from .fake_lcu import FakeLcu
from .gamedata_fixture import FixtureClient, loaded_gamedata
from .harness import Harness, credentials_for, wait_until

PAGES = "/lol-perks/v1/pages"


# --- Lectura de la selección ----------------------------------------------------------------


def test_hover_on_your_turn_is_the_preview() -> None:
    me = parse_session(DraftScenario().state(4, hover=YASUO)).me
    assert me is not None and not me.locked
    assert me.intent_id == YASUO and me.shown_champion == YASUO


def test_declared_intent_before_your_turn_counts() -> None:
    """Fase de planificación: el cliente solo lo pone en ``championPickIntent``."""
    mine = player(2, 0, position="middle", intent=AHRI)
    state = session(
        my_team=[player(0, position="top"), player(1, position="jungle"), mine],
        their_team=[player(5, team=2)],
        actions=[[action(100, 2, "pick")]],
        phase="PLANNING",
    )
    me = parse_session(state).me
    assert me is not None and me.champion_id == 0 and me.intent_id == AHRI


def test_locked_champion_is_shown_and_enemies_have_no_intent() -> None:
    snap = parse_session(DraftScenario().state(5))
    me = snap.me
    assert me is not None and me.locked and me.intent_id == 0 and me.shown_champion == AHRI
    assert all(s.intent_id == 0 for s in snap.enemies)


def test_with_my_champion_only_changes_you() -> None:
    snap = parse_session(DraftScenario().state(2))
    moved = snap.with_my_champion(ZED)
    assert moved.me is not None and moved.me.champion_id == ZED and moved.me.locked
    assert moved.allies == snap.allies and moved.enemies == snap.enemies


# --- Eventos ---------------------------------------------------------------------------------


async def test_preview_event_follows_what_you_look_at() -> None:
    bus = EventBus()
    seen: list[int] = []
    bus.subscribe(MyPickPreviewChanged, lambda e: seen.append(e.champion_id))
    tracker = ChampSelectTracker(bus)
    scenario = DraftScenario()
    for state in (
        scenario.state(2),
        scenario.state(3, hover=ZED),
        scenario.state(3, hover=ZED),  # tic del temporizador: nada
        scenario.state(4, hover=AHRI),
        scenario.state(5),  # bloqueas Ahri: el mismo campeón, nada nuevo
        scenario.state(10),
    ):
        await tracker.update(parse_session(state))
    assert seen == [ZED, AHRI]


async def test_presenter_shows_the_preview_until_the_real_loadout() -> None:
    bus = EventBus()
    gd = loaded_gamedata()
    presenter = UiPresenter(bus, gd)
    snapshot = parse_session(DraftScenario().state(4, hover=AHRI)).with_my_champion(AHRI)
    loadout = await LoadoutRecommender(gd).recommend(FixtureClient(), snapshot, None)
    assert loadout is not None and loadout.runes is not None

    await bus.publish(ChampSelectStarted(snapshot, None))
    await bus.publish(LoadoutPreview(AHRI, loadout))
    state = presenter.state
    assert state["champion"]["name"] == "Ahri" and state["champion"]["preview"]
    assert state["champion"]["position"] and state["loadout"]["preview"]
    assert state["loadout"]["page"]["keystone"]["id"] and len(state["loadout"]["spells"]) == 2

    await bus.publish(LoadoutPreview(0, None))  # deja de mirar
    assert presenter.state["champion"] is None and presenter.state["loadout"] is None

    await bus.publish(LoadoutReady(loadout))
    await bus.publish(LoadoutPreview(ZED, None))  # tarde: ya están las de verdad
    assert "preview" not in presenter.state["loadout"]


# --- De punta a punta --------------------------------------------------------------------------


async def test_a_quick_preview_writes_nothing_to_the_client(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
        await fake.set_phase("ChampSelect")
        await wait_until(lambda: endpoints.CHAMP_SELECT_SESSION in fake.requests)
        scenario = DraftScenario()
        await fake.set_session(scenario.state(4, hover=AHRI))
        await wait_until(lambda: any(e.champion_id == AHRI for e in app.of(LoadoutPreview)))
        preview = next(e for e in app.of(LoadoutPreview) if e.champion_id == AHRI)
        assert preview.loadout is not None and preview.loadout.runes is not None
        assert fake.writes_to(PAGES) == []
        assert fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION) == []

        for picks in range(5, 11):
            await fake.set_session(scenario.state(picks))
        await wait_until(lambda: bool(app.of(LoadoutApplied)))
    applied = app.of(LoadoutApplied)[0]
    assert applied.runes_applied and fake.writes_to(PAGES)
