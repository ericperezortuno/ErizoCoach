"""Baneos recomendados al mantener la preselección de un campeón."""

from __future__ import annotations

import asyncio

from erizocoach.bus import EventBus
from erizocoach.champselect.models import ChampSelectPhase, ChampSelectSnapshot, PlayerSlot
from erizocoach.champselect.parser import parse_session
from erizocoach.events import (
    BansUpdated,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    ChampSelectUpdated,
    MyPickPreviewChanged,
)
from erizocoach.loadout.bans import BanService, bans_for, unavailable_bans
from erizocoach.models import Position, QueueInfo
from erizocoach.ui.web.presenter import UiPresenter

from .gamedata_fixture import loaded_gamedata
from .test_counters import (
    AHRI,
    ANNIE,
    DARIUS,
    LUX,
    SYNDRA,
    YASUO,
    ZED,
    positions_for,
    source_with,
)

RANKED = QueueInfo(420, "Clasificatoria", "CLASSIC", 11)
DELAY = 0.02


def ahri_matchups():  # type: ignore[no-untyped-def]
    """Ahri en medio: (rival, partidas, victorias de Ahri)."""
    return source_with(
        {
            (AHRI, "MIDDLE"): (
                (ZED, 40, 14),  # 35 %, muy jugado
                (YASUO, 30, 12),  # 40 %
                (SYNDRA, 10, 2),  # 20 %, pero sale poco
                (LUX, 50, 27),  # Ahri le gana: no se banea
                (ANNIE, 4, 0),  # muy pocas partidas
            ),
        },
        {(AHRI, "MIDDLE"): (500, 250)},
    )


def test_bans_are_the_rivals_that_cost_you_the_most_wins() -> None:
    picks = bans_for(AHRI, Position.MIDDLE, ahri_matchups())
    assert [p.champion_id for p in picks] == [ZED, YASUO, SYNDRA]
    zed = picks[0]
    assert (zed.games, zed.wins) == (40, 14)
    assert zed.win_rate == 0.4  # (14 + 20 × 0,5) / (40 + 20)
    assert zed.share == round(40 / 134, 4)


def test_banned_or_ally_champions_are_never_suggested() -> None:
    picks = bans_for(AHRI, Position.MIDDLE, ahri_matchups(), unavailable={ZED})
    assert [p.champion_id for p in picks] == [YASUO, SYNDRA]
    assert bans_for(AHRI, Position.TOP, ahri_matchups()) == ()  # sin datos en esa línea


def slot(cell: int, *, me: bool = False, intent: int = 0, champion: int = 0,
         ally: bool = True) -> PlayerSlot:  # fmt: skip
    position = Position.MIDDLE if me else Position.NONE
    return PlayerSlot(cell, ally, me, champion, champion > 0, position, intent_id=intent)


def planning(
    mine: int = AHRI, *, ally_intent: int = 0, bans: tuple[int, ...] = (), pending: bool = True
) -> ChampSelectSnapshot:
    team = (slot(0, me=True, intent=mine), slot(1, intent=ally_intent))
    return ChampSelectSnapshot(
        1, ChampSelectPhase.PLANNING, team, (slot(5, ally=False),),
        enemy_bans=bans, my_ban_pending=pending,
    )  # fmt: skip


def test_what_your_allies_hover_or_took_is_left_out() -> None:
    snapshot = planning(ally_intent=ZED, bans=(YASUO,))
    assert unavailable_bans(snapshot) >= {ZED, YASUO}


def service() -> tuple[EventBus, list[BansUpdated]]:
    bus = EventBus()
    seen: list[BansUpdated] = []
    bus.subscribe(BansUpdated, seen.append)
    BanService(bus, positions_for, ahri_matchups(), delay=DELAY)
    return bus, seen


async def hover(bus: EventBus, snapshot: ChampSelectSnapshot) -> None:
    me = snapshot.me
    assert me is not None
    await bus.publish(MyPickPreviewChanged(me.shown_champion, snapshot))
    await bus.publish(ChampSelectUpdated(snapshot))


async def test_bans_appear_after_holding_the_pre_pick() -> None:
    bus, seen = service()
    await bus.publish(ChampSelectStarted(planning(0), RANKED))
    await hover(bus, planning())
    assert not any(e.picks for e in seen)  # aún no han pasado los 3 s
    await asyncio.sleep(DELAY * 3)
    assert seen[-1].champion_id == AHRI and seen[-1].position == Position.MIDDLE
    assert [p.champion_id for p in seen[-1].picks] == [ZED, YASUO, SYNDRA]

    # Otro baneo (o lo que mira un aliado) cambia la lista al momento
    await bus.publish(ChampSelectUpdated(planning(ally_intent=ZED)))
    assert [p.champion_id for p in seen[-1].picks] == [YASUO, SYNDRA]

    # Ya has baneado: fuera
    await bus.publish(ChampSelectUpdated(planning(pending=False)))
    assert seen[-1].champion_id == 0

    await bus.publish(ChampSelectEnded(ChampSelectOutcome.CANCELLED, planning(), RANKED))
    assert seen[-1].champion_id == 0


async def test_switching_champions_quickly_computes_nothing() -> None:
    bus, seen = service()
    await bus.publish(ChampSelectStarted(planning(0), RANKED))
    await hover(bus, planning())
    await asyncio.sleep(DELAY / 2)
    await hover(bus, planning(DARIUS))  # cambias antes de tiempo: sin datos de Darius
    await asyncio.sleep(DELAY * 3)
    assert not any(e.picks for e in seen)

    await hover(bus, planning())
    await asyncio.sleep(DELAY * 3)
    assert seen[-1].champion_id == AHRI
    await hover(bus, planning(DARIUS))  # el de antes deja de valer al momento
    assert seen[-1].champion_id == 0


async def test_no_bans_without_a_ban_of_yours_or_outside_the_rift() -> None:
    bus, seen = service()
    await bus.publish(ChampSelectStarted(planning(0, pending=False), RANKED))
    await hover(bus, planning(pending=False))
    await asyncio.sleep(DELAY * 3)
    assert not any(e.picks for e in seen)

    bus, seen = service()
    aram = QueueInfo(450, "ARAM", "ARAM", 12)
    await bus.publish(ChampSelectStarted(planning(0), aram))
    await hover(bus, planning())
    await asyncio.sleep(DELAY * 3)
    assert not any(e.picks for e in seen)


def test_the_parser_knows_if_your_ban_is_still_pending() -> None:
    def session(completed: bool) -> dict[str, object]:
        return {
            "localPlayerCellId": 0,
            "myTeam": [{"cellId": 0, "assignedPosition": "middle", "championPickIntent": AHRI}],
            "theirTeam": [],
            "actions": [[
                {"type": "ban", "actorCellId": 0, "completed": completed, "championId": 0},
                {"type": "ban", "actorCellId": 5, "completed": False, "championId": 0},
                {"type": "pick", "actorCellId": 0, "completed": False, "championId": AHRI},
            ]],
            "timer": {"phase": "PLANNING"},
        }  # fmt: skip

    assert parse_session(session(False)).my_ban_pending
    assert not parse_session(session(True)).my_ban_pending
    assert not parse_session({"localPlayerCellId": 0, "myTeam": []}).my_ban_pending


async def test_the_window_shows_the_bans() -> None:
    bus = EventBus()
    presenter = UiPresenter(bus, loaded_gamedata())
    picks = bans_for(AHRI, Position.MIDDLE, ahri_matchups())
    await bus.publish(BansUpdated(AHRI, Position.MIDDLE, picks))
    shown = presenter.state["bans"]
    assert shown["champion"]["name"] == "Ahri" and shown["champion"]["position"] == "Medio"
    assert shown["picks"][0] == {
        "id": ZED, "name": "Zed", "winRate": 40, "share": 30, "games": 40, "wins": 14,
    }  # fmt: skip
    await bus.publish(BansUpdated(0, Position.NONE, ()))
    assert presenter.state["bans"] is None
