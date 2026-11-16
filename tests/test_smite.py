"""Aplastar: obligatorio yendo a la jungla y prohibido en cualquier otra posición."""

from __future__ import annotations

import ssl

from erizocoach.bus import EventBus
from erizocoach.champselect.parser import parse_session
from erizocoach.events import (
    ChampSelectStarted,
    LoadoutInClient,
    LoadoutPreview,
    SpellsEditRequested,
)
from erizocoach.lcu import endpoints
from erizocoach.loadout.models import MatchContext
from erizocoach.loadout.rules import (
    FLASH,
    HEAL,
    IGNITE,
    SMITE,
    TELEPORT,
    SmiteRule,
    enforce_smite,
    smite_problem,
    smite_rule,
)
from erizocoach.models import GameflowPhase, Position, QueueInfo
from erizocoach.ui.web.presenter import UiPresenter

from .factories import AHRI, DraftScenario
from .fake_lcu import FakeLcu
from .gamedata_fixture import loaded_gamedata
from .harness import Harness, credentials_for, wait_until

RANKED = QueueInfo(420, "Clasificatoria", "CLASSIC", 11)


def test_the_rule_depends_on_your_position() -> None:
    assert smite_rule(Position.JUNGLE, 11) == SmiteRule.REQUIRED
    for position in (Position.TOP, Position.MIDDLE, Position.BOTTOM, Position.UTILITY):
        assert smite_rule(position, 11) == SmiteRule.FORBIDDEN
    assert smite_rule(Position.NONE, 11) == SmiteRule.FREE  # a ciegas o práctica: tú decides
    assert smite_rule(Position.JUNGLE, 12) == SmiteRule.FREE  # fuera de la Grieta


def test_problems() -> None:
    assert smite_problem((FLASH, IGNITE), SmiteRule.REQUIRED)
    assert not smite_problem((SMITE, FLASH), SmiteRule.REQUIRED)
    assert smite_problem((FLASH, SMITE), SmiteRule.FORBIDDEN)
    assert not smite_problem((FLASH, IGNITE), SmiteRule.FORBIDDEN)
    assert not smite_problem((FLASH, SMITE), SmiteRule.FREE)


def context(position: Position) -> MatchContext:
    return MatchContext(1, position, 11, "CLASSIC", None)  # sin rivales a la vista


def test_jungle_recommendations_always_carry_smite() -> None:
    gd = loaded_gamedata()
    spells, why = enforce_smite((IGNITE, FLASH), context(Position.JUNGLE), gd)
    assert set(spells) == {FLASH, SMITE} and why  # Destello se queda
    assert enforce_smite((FLASH, SMITE), context(Position.JUNGLE), gd) == ((FLASH, SMITE), [])


def test_other_positions_never_get_smite() -> None:
    gd = loaded_gamedata()
    for position, expected in ((Position.TOP, TELEPORT), (Position.MIDDLE, IGNITE),
                               (Position.BOTTOM, HEAL), (Position.UTILITY, IGNITE)):  # fmt: skip
        spells, why = enforce_smite((SMITE, FLASH), context(position), gd)
        assert spells == (expected, FLASH) and why
    spells, _ = enforce_smite((SMITE, IGNITE), context(Position.MIDDLE), gd)
    assert spells == (FLASH, IGNITE)  # sin Destello: primero Destello


async def test_the_window_knows_the_rule() -> None:
    gd = loaded_gamedata()
    state = DraftScenario().state(3)
    presenter = UiPresenter(EventBus(), gd)
    await presenter._on_champ_select(ChampSelectStarted(parse_session(state), RANKED))
    assert presenter.state["spellRule"] == "forbidden" and presenter.state["smiteId"] == SMITE
    me = next(p for p in state["myTeam"] if p["cellId"] == 2)
    me["assignedPosition"] = "jungle"
    await presenter._on_champ_select(ChampSelectStarted(parse_session(state), RANKED))
    assert presenter.state["spellRule"] == "required"


async def hovering(fake: FakeLcu, app: Harness, position: str, spells: tuple[int, int]) -> None:
    await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
    await fake.set_phase("ChampSelect")
    await wait_until(lambda: endpoints.CHAMP_SELECT_SESSION in fake.requests)
    state = DraftScenario().state(3, hover=AHRI)
    me = next(p for p in state["myTeam"] if p["cellId"] == 2)
    me["assignedPosition"] = position
    me["spell1Id"], me["spell2Id"] = spells
    await fake.set_session(state)
    await wait_until(lambda: any(e.champion_id == AHRI for e in app.of(LoadoutPreview)))


async def test_smite_is_refused_outside_the_jungle(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await hovering(fake, app, "middle", (4, 14))
        writes = len(fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION))
        await app.bus.publish(SpellsEditRequested((4, SMITE)))
        assert "jungla" in app.of(LoadoutInClient)[-1].problem
        assert len(fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION)) == writes


async def test_in_the_jungle_smite_cannot_be_removed(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    async with Harness(credentials_for(fake), client_ssl) as app:
        await hovering(fake, app, "jungle", (SMITE, 4))
        writes = len(fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION))
        await app.bus.publish(SpellsEditRequested((14, 4)))  # Prender en lugar de Aplastar
        assert "obligatorio" in app.of(LoadoutInClient)[-1].problem
        assert len(fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION)) == writes
        await app.bus.publish(SpellsEditRequested((4, SMITE)))  # cambiarlo de tecla, sí
        assert fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION)[-1][2] == {
            "spell1Id": 4,
            "spell2Id": SMITE,
        }
