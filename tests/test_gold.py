"""Oro total de cada jugador, como en las retransmisiones (valores de la wiki, 26.01-26.03)."""

from __future__ import annotations

from typing import Any

import pytest

from lolcoach.ingame.analysis import analyze, item_value
from lolcoach.ingame.gold import (
    FIRST_BLOOD_BONUS,
    JUNGLE_CS_GOLD,
    GoldTracker,
    kill_bounty,
    lane_cs_gold,
    passive_gold,
    turret_gold,
)
from lolcoach.ingame.models import CHAOS, ORDER, LiveGame, parse_all_game_data
from lolcoach.models import Position

from .gamedata_fixture import loaded_gamedata
from .live_factories import ME_RIOT_ID, all_game_data, live_player

PRICES = {1055: 450, 1056: 400, 3158: 900, 6655: 2900, 3031: 3450}
SMITE = {
    "summonerSpellOne": {
        "displayName": "Aplastar",
        "rawDisplayName": "GeneratedTip_SummonerSpell_SummonerSmite_DisplayName",
    },
    "summonerSpellTwo": {
        "displayName": "Destello",
        "rawDisplayName": "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName",
    },
}


def price(item_id: int) -> int:
    return PRICES.get(item_id, 0)


def lobby(
    *,
    t: float = 0,
    gold: float = 500,
    me: dict[str, Any] | None = None,
    stats: dict[str, dict[str, Any]] | None = None,
    turrets: tuple[str, ...] = (),
) -> LiveGame:
    """Ahri (tú, medio) y Zed enfrente; ``stats`` cambia a quien se quiera por campeón."""
    stats = stats or {}
    rows = [
        ("Ahri", ORDER, "MIDDLE"), ("Garen", ORDER, "TOP"), ("LeeSin", ORDER, "JUNGLE"),
        ("Jinx", ORDER, "BOTTOM"), ("Thresh", ORDER, "UTILITY"),
        ("Zed", CHAOS, "MIDDLE"), ("Darius", CHAOS, "TOP"), ("Graves", CHAOS, "JUNGLE"),
        ("Kaisa", CHAOS, "BOTTOM"), ("Soraka", CHAOS, "UTILITY"),
    ]  # fmt: skip
    players = []
    for alias, team, position in rows:
        values: dict[str, Any] = {"cs": 0, "level": 1} | stats.get(alias, {})
        if alias == "Ahri":
            values |= {"riot_id": ME_RIOT_ID} | (me or {})
        players.append(live_player(alias, team=team, position=position, **values))
    data = all_game_data(players[0], players[1:], game_time=t, gold=gold)
    data["events"]["Events"] += [
        {"EventID": n + 1, "EventName": "TurretKilled", "TurretKilled": name, "EventTime": t}
        for n, name in enumerate(turrets)
    ]
    return parse_all_game_data(data)


def by_alias(game: LiveGame, gold: dict[int, int]) -> dict[str, int]:
    return {p.champion_alias: gold[id(p)] for p in game.players}


def tracker() -> GoldTracker:
    return GoldTracker(lambda p: item_value(p, price))


# --- Valores ---------------------------------------------------------------------------


def test_passive_gold_starts_at_1_05() -> None:
    assert passive_gold(0) == passive_gold(65) == 500
    assert passive_gold(65 + 600) == pytest.approx(500 + 1224)  # 20,4 cada 10 s


def test_minions_are_worth_more_later() -> None:
    assert lane_cs_gold(300) == pytest.approx(18.7)
    assert lane_cs_gold(900) < lane_cs_gold(1200) < lane_cs_gold(1500) == pytest.approx(21.5)


def test_kill_bounty_follows_the_victims_level() -> None:
    assert kill_bounty(1) == kill_bounty(6) == 300
    assert kill_bounty(7) == 310 and kill_bounty(18) == kill_bounty(20) == 420


def test_turrets_pay_the_other_team_once() -> None:
    gold = turret_gold(
        [
            "Turret_T2_L_03_A",  # exterior de arriba del rojo: para el azul
            "Turret_T2_L_03_A",  # repetido: cuenta una vez
            "Turret_T2_C_05_A",  # exterior de medio
            "Turret_T2_R_02_A",  # interior de abajo
            "Turret_T1_C_01_A",  # del nexo del azul: para el rojo
            "Turret_T1_C_07_A",  # la de la base (no se puede destruir): nada
            "Barracks_T1_L1",
            "",
        ]
    )
    assert gold == {ORDER: 50 + 50 + 25, CHAOS: 50}


def test_events_give_the_destroyed_turrets_and_smite_marks_the_jungler() -> None:
    data = all_game_data(
        live_player("Ahri", riot_id=ME_RIOT_ID),
        [live_player("Graves", team=CHAOS) | {"summonerSpells": SMITE}],
    )
    data["events"]["Events"].append(
        {"EventID": 1, "EventName": "TurretKilled", "TurretKilled": "Turret_T2_L_03_A"}
    )
    game = parse_all_game_data(data)
    assert game.destroyed_turrets == ("Turret_T2_L_03_A",)
    assert game.enemies[0].has_smite and game.me is not None and not game.me.has_smite


# --- Seguimiento durante la partida --------------------------------------------------


def test_at_the_start_everybody_has_the_starting_gold() -> None:
    game = lobby(t=10)
    gold = tracker().update(game)
    assert set(gold.values()) == {500}


def test_minions_and_passive_gold_add_up_live() -> None:
    gold = tracker()
    start = lobby(t=100)
    zed_before = by_alias(start, gold.update(start))["Zed"]
    game = lobby(t=400, stats={"Zed": {"cs": 30}})
    after = by_alias(game, gold.update(game))
    assert after["Zed"] - zed_before == round(30 * 18.7 + 300 * 2.04)
    assert after["Darius"] == round(passive_gold(400))  # sin súbditos: solo el pasivo


def test_jungle_monsters_are_valued_as_jungle_camps() -> None:
    gold = tracker()
    gold.update(lobby(t=100))
    game = lobby(t=400, stats={"Graves": {"cs": 30}, "Zed": {"cs": 30}})
    after = by_alias(game, gold.update(game))
    assert after["Graves"] - after["Zed"] == round(30 * (JUNGLE_CS_GOLD - 18.7))


def test_kills_assists_and_first_blood() -> None:
    gold = tracker()
    gold.update(lobby(t=200))
    game = lobby(
        t=201,
        stats={
            "Zed": {"kda": (1, 0, 0)},
            "Graves": {"kda": (0, 0, 1)},
            "Ahri": {"kda": (0, 1, 0)},
        },
    )
    after = by_alias(game, gold.update(game))
    base = round(passive_gold(201))
    assert after["Zed"] - base == 300 + FIRST_BLOOD_BONUS
    assert after["Graves"] - base == 150  # la mitad, para el único que ayudó
    assert after["Ahri"] == base  # morir no quita oro

    game = lobby(
        t=202,
        stats={
            "Zed": {"kda": (2, 0, 0)},
            "Graves": {"kda": (0, 0, 2)},
            "Soraka": {"kda": (0, 0, 1)},
            "Ahri": {"kda": (0, 2, 0)},
        },
    )
    again = by_alias(game, gold.update(game))
    step = round(passive_gold(202)) - base
    assert again["Zed"] - after["Zed"] - step == 300  # sin primera sangre otra vez
    assert again["Graves"] - after["Graves"] - step == 75  # 150 entre dos
    assert again["Soraka"] - base - step == 75


def test_kill_gold_grows_with_the_level_of_the_victims() -> None:
    gold = tracker()
    high = {alias: {"level": 18} for alias in ("Ahri", "Garen", "LeeSin", "Jinx", "Thresh")}
    gold.update(lobby(t=1800, stats=high))
    game = lobby(t=1800, stats=high | {"Zed": {"kda": (1, 0, 0)}})
    after = by_alias(game, gold.update(game))
    assert after["Zed"] - after["Darius"] == 420 + FIRST_BLOOD_BONUS  # es la primera


def test_turrets_add_global_gold_to_the_whole_team() -> None:
    gold = tracker()
    game = lobby(t=600, turrets=("Turret_T2_R_03_A",))
    after = by_alias(game, gold.update(game))
    assert after["Thresh"] - after["Soraka"] == 50 and after["Garen"] - after["Darius"] == 50


def test_your_gold_is_exact() -> None:
    game = lobby(t=300, gold=1234, me={"items": (1056, 3158)})
    after = by_alias(game, tracker().update(game))
    assert after["Ahri"] == 400 + 900 + 1234  # objetos + oro sin gastar


def test_what_cannot_be_seen_is_corrected_when_they_buy() -> None:
    """Placas, recompensas... no se ven, pero el valor de lo que compran las delata."""
    gold = tracker()
    gold.update(lobby(t=300))
    rich = lobby(t=300, stats={"Zed": {"items": (6655, 3158)}})  # 3800 en objetos
    assert by_alias(rich, gold.update(rich))["Zed"] == 3800
    later = lobby(t=310, stats={"Zed": {"items": (6655, 3158), "cs": 2}})
    zed = by_alias(later, gold.update(later))["Zed"]
    # Sigue sumando desde ahí: la corrección se queda para el resto de la partida
    assert zed == round(3800 + 10 * 2.04 + 2 * 18.7)


def test_selling_or_drinking_does_not_lower_the_total() -> None:
    gold = tracker()
    with_items = lobby(t=300, stats={"Zed": {"items": (6655,)}})
    before = by_alias(with_items, gold.update(with_items))["Zed"]
    sold = lobby(t=301)
    assert by_alias(sold, gold.update(sold))["Zed"] >= before


def test_opening_the_app_mid_game_values_what_they_already_have() -> None:
    game = lobby(t=1200, stats={"Zed": {"kda": (4, 0, 2), "cs": 150, "level": 13}})
    zed = by_alias(game, tracker().update(game))["Zed"]
    expected = passive_gold(1200) + 150 * (18.7 + lane_cs_gold(1200)) / 2 + 4 * 300
    assert zed == pytest.approx(expected + 2 * 150 * 0.5, abs=150)


def test_a_new_game_starts_from_zero() -> None:
    gold = tracker()
    rich = lobby(t=1500, stats={"Zed": {"items": (6655, 3031)}})
    gold.update(rich)
    fresh = lobby(t=20)
    assert set(gold.update(fresh).values()) == {500}


# --- En el análisis --------------------------------------------------------------------


def test_lane_and_team_differences_use_the_total_gold() -> None:
    gd = loaded_gamedata()
    game = lobby(t=600, gold=300, stats={"Zed": {"kda": (2, 0, 0), "cs": 60}})
    total = tracker().update(game)
    report = analyze(
        game,
        champion_id=gd.champion_id_by_alias,
        item_price=price,
        profiles={},
        positions_for=gd.positions_for,
        total_gold=total,
    )
    zed = next(r for r in report.enemies if r.player.champion_alias == "Zed")
    me = report.me
    assert me is not None and me.position == Position.MIDDLE
    assert zed.gold == total[id(zed.player)] and zed.item_gold == 0
    assert me.gold_vs_lane_rival == total[id(game.me)] - total[id(zed.player)] < -700
    assert report.team_gold == sum(total[id(p)] for p in (game.me, *game.allies))
    assert report.enemy_gold == sum(total[id(p)] for p in game.enemies)


def test_without_the_tracker_item_gold_is_used() -> None:
    gd = loaded_gamedata()
    game = lobby(t=600, stats={"Zed": {"items": (6655,)}})
    report = analyze(
        game,
        champion_id=gd.champion_id_by_alias,
        item_price=price,
        profiles={},
        positions_for=gd.positions_for,
    )
    zed = next(r for r in report.enemies if r.player.champion_alias == "Zed")
    assert zed.gold == zed.item_gold == 2900


def test_players_are_told_apart_even_with_the_same_champion() -> None:
    """En modos con campeones repetidos (Uno para todos) cada uno lleva su cuenta."""
    data = all_game_data(
        live_player("Ahri", riot_id=ME_RIOT_ID, cs=0),
        [live_player("Ahri", cs=0), live_player("Zed", team=CHAOS, cs=0)],
        game_time=100,
    )
    gold = tracker()
    gold.update(parse_all_game_data(data))
    data["allPlayers"][1]["scores"]["creepScore"] = 10
    data["gameData"]["gameTime"] = 101
    game = parse_all_game_data(data)
    after = gold.update(game)
    assert after[id(game.allies[0])] - after[id(game.enemies[0])] == round(10 * 18.7)
