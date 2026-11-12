from __future__ import annotations

import pytest

from lolcoach.champselect.models import ChampSelectPhase, Position
from lolcoach.champselect.parser import parse_session

from .factories import (
    AHRI,
    DARIUS,
    GRAVES,
    JINX,
    LEE_SIN,
    YASUO,
    ZED,
    DraftScenario,
    aram_session,
    blind_session,
)


def test_draft_mid_pick_hover_is_not_locked() -> None:
    snap = parse_session(DraftScenario().state(4, hover=YASUO))
    me = snap.me
    assert me is not None
    assert me.cell_id == 2 and me.position == Position.MIDDLE
    assert me.champion_id == YASUO and not me.locked  # solo lo está mirando
    assert [s.champion_id for s in snap.allies if s.locked] == [86, LEE_SIN]
    assert [s.champion_id for s in snap.known_enemies] == [DARIUS, GRAVES]
    assert not snap.is_finalized
    assert snap.phase == ChampSelectPhase.BAN_PICK


def test_draft_finalized() -> None:
    snap = parse_session(DraftScenario().state(10))
    assert snap.is_finalized
    assert snap.me is not None and snap.me.champion_id == AHRI and snap.me.locked
    assert len(snap.known_enemies) == 5
    assert all(s.position == Position.NONE for s in snap.enemies)  # el cliente no lo revela
    assert snap.ally_bans == (157, 17, 99, 25, 1)
    assert snap.enemy_bans == (2, 3, 4, 5, 6)


def test_all_picks_done_counts_as_finalized_even_before_phase_change() -> None:
    snap = parse_session(DraftScenario().state(10, phase="BAN_PICK"))
    assert snap.is_finalized


def test_trade_after_lock_uses_team_champion() -> None:
    data = DraftScenario().state(10)
    data["myTeam"][2]["championId"] = JINX  # intercambio con el ADC
    data["myTeam"][3]["championId"] = AHRI
    snap = parse_session(data)
    assert snap.me is not None and snap.me.champion_id == JINX and snap.me.locked


def test_blind_pick_enemies_unknown() -> None:
    snap = parse_session(blind_session(my_locked=True))
    assert snap.is_finalized
    assert not snap.enemies_known
    assert snap.me is not None and snap.me.champion_id == AHRI and snap.me.locked


def test_blind_pick_not_finalized_until_everyone_locks() -> None:
    snap = parse_session(blind_session(my_locked=False))
    assert not snap.is_finalized
    assert snap.me is not None and not snap.me.locked


def test_aram_assigned_champion_counts_as_locked() -> None:
    snap = parse_session(aram_session(ZED))
    assert snap.bench_enabled and not snap.has_pick_actions
    assert snap.me is not None and snap.me.champion_id == ZED and snap.me.locked
    assert snap.is_finalized


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"myTeam": None, "theirTeam": "x", "actions": [[None, 3]], "timer": None},
        {"myTeam": [{"cellId": "2", "championId": "103"}], "localPlayerCellId": 2},
    ],
)
def test_tolerates_missing_or_wrong_fields(broken: dict) -> None:
    snap = parse_session(broken)
    assert snap.phase in (ChampSelectPhase.UNKNOWN,)


def test_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        parse_session(["no", "es", "un", "objeto"])


def test_snapshot_ignores_timer_changes() -> None:
    a = DraftScenario().state(3)
    b = DraftScenario().state(3)
    b["timer"]["adjustedTimeLeftInPhase"] = 1234
    assert parse_session(a) == parse_session(b)
