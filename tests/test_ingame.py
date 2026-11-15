"""Partida en vivo: lectura, quién va fuerte y qué comprar (datos reales del 16.22)."""

from __future__ import annotations

import dataclasses

import pytest

from lolcoach.builds.models import BuildPlan
from lolcoach.builds.recommender import BuildRecommender
from lolcoach.gamedata.catalog import DamageType
from lolcoach.ingame.advisor import advise, purchase_order
from lolcoach.ingame.analysis import GameReport, Strength, analyze
from lolcoach.ingame.models import LiveGame, parse_all_game_data
from lolcoach.ingame.role_slot import HiddenBoots, RoleSlotBoots
from lolcoach.models import Position

from .gamedata_fixture import FixtureClient, loaded_gamedata
from .live_factories import ME_RIOT_ID, all_game_data, bot_game, live_player, standard_game

AHRI, ZED, SORAKA = 103, 238, 16
ENEMY_IDS = [122, 104, 238, 145, 16]  # Darius, Graves, Zed, Kai'Sa, Soraka
LUDEN, ZHONYA, OBLIVION_ORB, SORCERER, IONIAN = 6655, 3157, 3916, 3020, 3158
FED_ZED_ITEMS = (1055, 3158, 3142, 6697)  # Doran, botas, Youmuu, Soberbia


def report_for(data: dict, my_position: Position = Position.MIDDLE) -> tuple[LiveGame, GameReport]:
    gd = loaded_gamedata()
    game = parse_all_game_data(data)
    report = analyze(
        game,
        champion_id=gd.champion_id_by_alias,
        item_price=lambda i: item.price_total if (item := gd.item(i)) else 0,
        profiles=gd._profiles,
        positions_for=gd.positions_for,
        my_position=my_position,
    )
    return game, report


async def ahri_plan() -> BuildPlan:
    gd = loaded_gamedata()
    plan = await BuildRecommender(gd).recommend_for(
        FixtureClient(), AHRI, Position.MIDDLE, ENEMY_IDS, 11
    )
    assert plan is not None
    return plan


def advice_for(plan: BuildPlan, data: dict):
    gd = loaded_gamedata()
    game, report = report_for(data)
    assert game.me is not None
    return advise(
        plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name,
        game_time=game.game_time,
    )  # fmt: skip


# --- Lectura -------------------------------------------------------------------------


def test_parse_identifies_you_and_the_teams() -> None:
    game = parse_all_game_data(standard_game(gold=812.4))
    assert game.me is not None and game.me.champion_alias == "Ahri"
    assert game.me.position == Position.MIDDLE and game.me.item_ids == (1056, 3020)
    assert len(game.allies) == 4 and len(game.enemies) == 5
    assert {p.champion_alias for p in game.enemies} >= {"Zed", "Kaisa"}
    assert game.current_gold == pytest.approx(812.4) and game.map_id == 11
    assert ME_RIOT_ID not in repr(game) and "Jugador" not in repr(game)  # sin Riot ID


def test_champion_is_recognised_in_every_raw_name_format() -> None:
    """Riot da a Aatrox «Character_Aatrox_Name» en vez de «game_character_displayname_Aatrox»."""
    gd = loaded_gamedata()
    formats = [
        {"rawChampionName": "game_character_displayname_Ahri"},
        {"rawChampionName": "Character_Aatrox_Name"},
        {"rawChampionName": "algo_nuevo", "rawSkinName": "game_character_displayname_Kaisa"},
        {"rawChampionName": "", "championName": "Kai'Sa"},  # último recurso: nombre visible
        {"rawChampionName": "", "championName": "Dr. Mundo"},
    ]
    expected = [103, 266, 145, 145, 36]
    for raw, champion_id in zip(formats, expected, strict=True):
        game = parse_all_game_data(all_game_data(live_player("X") | raw, []))
        assert game.me is not None
        assert gd.champion_id_by_alias(game.me.champion_alias) == champion_id, raw
    assert gd.champion_id_by_alias("NoExiste") == 0


def test_parse_tolerates_missing_data() -> None:
    data = all_game_data(live_player("Ahri"), [live_player("Zed", team="CHAOS")])
    del data["activePlayer"]
    game = parse_all_game_data(data)
    assert game.me is None and len(game.enemies) == 1
    with pytest.raises(ValueError):
        parse_all_game_data(["no", "es", "un", "objeto"])


# --- Quién va fuerte ------------------------------------------------------------------------


def test_nobody_is_labelled_in_the_first_minutes() -> None:
    _, report = report_for(standard_game(game_time=120))
    assert all(r.strength == Strength.EVEN for r in report.enemies)
    assert report.top_threat is None


def test_fed_lane_opponent_is_the_top_threat() -> None:
    _, report = report_for(standard_game(zed_kda=(8, 1, 2), zed_items=FED_ZED_ITEMS))
    threat = report.top_threat
    assert threat is not None and threat.champion_id == ZED
    assert threat.strength == Strength.VERY_STRONG and threat.is_lane_opponent
    assert threat.damage_type == DamageType.PHYSICAL and threat.gold_vs_average > 1000


def test_player_behind() -> None:
    data = standard_game()
    soraka = next(p for p in data["allPlayers"] if p["championName"] == "Soraka")
    soraka["scores"].update(kills=0, deaths=6, assists=1)
    _, report = report_for(data)
    behind = next(r for r in report.enemies if r.champion_id == SORAKA)
    assert behind.strength == Strength.BEHIND


def test_lane_opponent_is_inferred_when_the_game_gives_no_positions() -> None:
    data = standard_game()
    for player in data["allPlayers"]:
        player["position"] = ""
    _, report = report_for(data, my_position=Position.MIDDLE)
    lane = [r for r in report.enemies if r.is_lane_opponent]
    assert len(lane) == 1 and lane[0].champion_id == ZED


# --- Qué comprar ----------------------------------------------------------------------------


async def test_follows_the_build_order() -> None:
    plan = await ahri_plan()
    advice = advice_for(plan, standard_game(my_items=(1056, SORCERER), gold=900))
    assert advice.next_purchase is not None and advice.next_purchase.item_id == LUDEN
    assert "núcleo" in advice.next_purchase.reason
    assert advice.build[0].owned  # botas ya compradas
    assert not advice.next_purchase.can_buy_now
    assert advice.next_purchase.remaining_gold > 0


async def test_first_core_item_comes_before_boots() -> None:
    plan = await ahri_plan()
    assert purchase_order(plan)[:2] == [LUDEN, SORCERER]
    advice = advice_for(plan, standard_game(my_items=(1056, 2003), gold=500))
    assert advice.next_purchase is not None and advice.next_purchase.item_id == LUDEN
    after_first = advice_for(plan, standard_game(my_items=(1056, LUDEN), gold=500))
    assert after_first.next_purchase is not None
    assert after_first.next_purchase.item_id == SORCERER


def test_real_practice_tool_sample() -> None:
    """Datos reales del juego (Herramienta de práctica, anonimizados)."""
    import json
    from pathlib import Path

    data = json.loads(
        (Path(__file__).parent / "fixtures" / "live_practice_tool.json").read_text("utf-8")
    )
    game, report = report_for(data, my_position=Position.MIDDLE)
    assert game.me is not None and game.me.champion_alias == "Akali"
    assert game.me.position == Position.NONE  # el juego envía "NONE" en la práctica
    assert game.game_mode == "PRACTICETOOL" and game.map_id == 11
    assert [p.champion_alias for p in game.enemies] == ["Kennen"] and game.enemies[0].is_bot
    assert report.me is not None and report.me.champion_id == 84
    assert report.enemies[0].champion_id == 85 and report.enemies[0].is_lane_opponent


SHADOWFLAME, RABADON, MORELLO, STOPWATCH = 4645, 3089, 3165, 2420
AHRI_CORE = (SORCERER, LUDEN, SHADOWFLAME, RABADON)  # botas + núcleo completo
LANING, AFTER_LANING = 10 * 60, 20 * 60


def feed(data: dict, alias: str, kda: tuple[int, int, int], items: tuple[int, ...]) -> None:
    player = next(p for p in data["allPlayers"] if p["championName"] == alias)
    player["scores"].update(kills=kda[0], deaths=kda[1], assists=kda[2])
    player["items"] = [{"itemID": i, "count": 1, "slot": n} for n, i in enumerate(items)]
    player["level"] = 13


def fed_soraka(data: dict) -> None:
    feed(data, "Soraka", (4, 0, 12), (3865, 3158, 6617, 3107))


async def test_urgent_defense_against_a_fed_enemy() -> None:
    plan = await ahri_plan()
    data = standard_game(
        zed_kda=(8, 1, 2), zed_items=FED_ZED_ITEMS, my_items=AHRI_CORE, game_time=AFTER_LANING
    )
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == ZHONYA
    assert "Zed" in purchase.reason and "físico" in purchase.reason


async def test_the_core_comes_before_any_defense() -> None:
    """Con el núcleo a medias, ni un Zed que va muy fuerte te desvía de él."""
    plan = await ahri_plan()
    data = standard_game(zed_kda=(10, 0, 3), zed_items=(*FED_ZED_ITEMS, 6694), my_items=(1056,))
    advice = advice_for(plan, data)
    assert advice.next_purchase is not None and advice.next_purchase.item_id == LUDEN
    assert advice.threat_id == 0
    data = standard_game(
        zed_kda=(10, 0, 3), zed_items=(*FED_ZED_ITEMS, 6694), my_items=(SORCERER, LUDEN)
    )
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == SHADOWFLAME


async def test_no_urgent_defense_if_you_already_have_it() -> None:
    plan = await ahri_plan()
    data = standard_game(
        zed_kda=(8, 1, 2), zed_items=FED_ZED_ITEMS, my_items=(*AHRI_CORE, ZHONYA),
        game_time=AFTER_LANING,
    )  # fmt: skip
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == MORELLO


async def test_a_started_item_is_finished_first() -> None:
    """Si ya tienes el Reloj (componente de Zhonya), se termina Zhonya antes que Llamasombría."""
    plan = await ahri_plan()
    data = standard_game(my_items=(SORCERER, LUDEN, STOPWATCH))
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == ZHONYA
    assert "empezado" in purchase.reason


async def test_a_started_item_is_finished_before_answering_a_threat() -> None:
    plan = await ahri_plan()
    rod = 1058  # Vara innecesariamente grande: va a Rabadon (el siguiente) y a Zhonya
    data = standard_game(
        zed_kda=(10, 0, 3), zed_items=(*FED_ZED_ITEMS, 6694),
        my_items=(SORCERER, LUDEN, SHADOWFLAME, rod), game_time=AFTER_LANING,
    )  # fmt: skip
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == RABADON


async def test_in_laning_phase_only_your_lane_matters() -> None:
    """A los 10 minutos, con el núcleo hecho, un Darius (arriba) muy fuerte no te cambia la
    build; tu rival de línea (Zed), sí."""
    plan = await ahri_plan()
    data = standard_game(my_items=AHRI_CORE, game_time=LANING)
    feed(data, "Darius", (9, 0, 2), (3047, 3071, 3053, 6333))
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == MORELLO  # sigue tu build
    data = standard_game(
        zed_kda=(8, 1, 2), zed_items=FED_ZED_ITEMS, my_items=AHRI_CORE, game_time=LANING
    )
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == ZHONYA


async def test_anti_heal_component_when_the_healer_is_strong() -> None:
    plan = await ahri_plan()
    data = standard_game(my_items=(1056, SORCERER), game_time=AFTER_LANING)
    fed_soraka(data)
    purchase = advice_for(plan, data).next_purchase
    # Soraka hace poco daño (1/3): no tiene sentido comprar resistencia mágica por ella.
    # Es la única excepción al núcleo, y solo el componente, no el Morellonomicón
    assert purchase is not None and purchase.item_id == OBLIVION_ORB
    assert "Soraka" in purchase.reason and "cura" in purchase.reason


async def test_no_anti_heal_in_laning_phase_against_another_lane() -> None:
    plan = await ahri_plan()
    data = standard_game(my_items=(1056, SORCERER), game_time=LANING)
    fed_soraka(data)  # Soraka juega abajo: Ahri (medio) no se cruza con ella
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == LUDEN


async def test_anti_heal_component_against_your_lane_in_laning_phase() -> None:
    """Kai'Sa abajo contra Soraka: el Llamado del verdugo antes que el núcleo, aunque Soraka
    no vaya fuerte (se cura muchísimo); luego vuelve al núcleo, no al Recordatorio letal."""
    plan = await kaisa_plan()
    gd = loaded_gamedata()
    executioners, mortal_reminder = 3123, 3033
    advice = bot_advice(plan, bot_game((1055,), gold=900, game_time=LANING), hidden=False)
    assert advice.next_purchase is not None
    assert advice.next_purchase.item_id == executioners
    assert "en tu línea" in advice.next_purchase.reason
    assert gd.item(executioners).price_total < 1000  # solo el componente
    after = bot_advice(plan, bot_game((1055, executioners), gold=900, game_time=LANING), False)
    assert after.next_purchase is not None and after.next_purchase.item_id == KRAKEN
    assert after.next_purchase.item_id != mortal_reminder


async def test_full_anti_heal_only_once_the_core_is_done() -> None:
    plan = await ahri_plan()
    data = standard_game(my_items=(*AHRI_CORE, OBLIVION_ORB), game_time=AFTER_LANING)
    fed_soraka(data)
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == MORELLO
    assert "antisanación" in purchase.reason


async def test_strongest_threat_decides_between_defense_and_anti_heal() -> None:
    plan = await ahri_plan()
    data = standard_game(
        zed_kda=(10, 0, 3), zed_items=(*FED_ZED_ITEMS, 6694), my_items=AHRI_CORE,
        game_time=AFTER_LANING,
    )  # fmt: skip
    feed(data, "Soraka", (2, 0, 8), (3865, 3158, 6617))
    purchase = advice_for(plan, data).next_purchase
    assert purchase is not None and purchase.item_id == ZHONYA  # Zed va mucho más fuerte


def test_bottom_lane_has_two_enemies() -> None:
    _, report = report_for(bot_game((1055,)), my_position=Position.BOTTOM)
    in_lane = {e.player.champion_alias for e in report.enemies if e.in_lane}
    assert in_lane == {"Jhin", "Soraka"}
    _, report = report_for(standard_game())
    assert {e.player.champion_alias for e in report.enemies if e.in_lane} == {"Zed"}


async def test_affordability_and_components() -> None:
    plan = await ahri_plan()
    gd = loaded_gamedata()
    rich = advice_for(plan, standard_game(my_items=(1056, SORCERER), gold=4000)).next_purchase
    assert rich is not None and rich.can_buy_now and rich.remaining_gold == 0
    # Con 900 de oro no llega para Capítulo perdido (1200) ni Alternador (1100):
    # baja por la receta y propone Tomo amplificador, Cristal de zafiro...
    poor = advice_for(plan, standard_game(my_items=(1056, SORCERER), gold=900)).next_purchase
    assert poor is not None and poor.affordable_components
    assert all(gd.item(c).price_total <= 900 for c in poor.affordable_components)
    assert poor.affordable_components[0] == 1052  # Tomo amplificador (400), el más caro
    assert poor.remaining_gold == 2750 - 900
    # Si ya tienes un Tomo, se descuenta aunque esté dos niveles por debajo
    tome = advice_for(plan, standard_game(my_items=(1056, SORCERER, 1052), gold=900))
    assert tome.next_purchase is not None and tome.next_purchase.remaining_gold == 2750 - 400 - 900


async def test_other_boots_count_as_your_boots() -> None:
    plan = await ahri_plan()
    advice = advice_for(plan, standard_game(my_items=(1056, IONIAN)))
    assert advice.build[0].item_id == SORCERER and advice.build[0].owned


# --- Botas en el espacio de la misión de rol (línea inferior) ------------------------------
# Al completar la misión de rol de tirador, las botas pasan a un espacio aparte que la API de
# la partida en vivo no publica (solo da los espacios 0-6): desaparecen de la lista de objetos.

KRAKEN, BERSERKER, BASIC_BOOTS, PICKAXE = 6672, 3006, 1001, 1037
MAGICAL_FOOTWEAR = 2422  # Calzado ligeramente mágico (runa)


async def kaisa_plan() -> BuildPlan:
    plan = await BuildRecommender(loaded_gamedata()).recommend_for(
        FixtureClient(), 145, Position.BOTTOM, ENEMY_IDS, 11
    )
    assert plan is not None and plan.boots == BERSERKER and plan.core[0] == KRAKEN
    return plan


def follow(*snapshots: dict) -> tuple[LiveGame, HiddenBoots]:
    """Pasa las lecturas en orden, como el monitor; devuelve la última y las botas ocultas."""
    tracker = RoleSlotBoots(loaded_gamedata().item)
    hidden = HiddenBoots.NONE
    game = parse_all_game_data(snapshots[0])
    for data in snapshots:
        game = parse_all_game_data(data)
        assert game.me is not None
        hidden = tracker.update(game.me, game.current_gold)
    return game, hidden


def bot_advice(plan: BuildPlan, data: dict, hidden: HiddenBoots | bool):
    """Como el monitor: unas botas de base ocultas cuentan como el componente."""
    gd = loaded_gamedata()
    game, report = report_for(data, my_position=Position.BOTTOM)
    assert game.me is not None
    owned = game.me.item_ids + ((BASIC_BOOTS,) if hidden == HiddenBoots.BASIC else ())
    return advise(
        plan, report, owned, game.current_gold, gd.item, gd.champion_name,
        game_time=game.game_time, boots_out_of_sight=hidden in (True, HiddenBoots.UPGRADED),
    )  # fmt: skip


async def test_boots_moved_to_the_role_quest_slot_still_count() -> None:
    """Lo que pasó con Kai'Sa: tras la misión pedía comprar unas Grebas que ya tenía."""
    plan = await kaisa_plan()
    before = bot_game((1055, KRAKEN, BERSERKER), gold=400)
    after = bot_game((1055, KRAKEN), gold=700)  # misión completada: +300 de oro, botas fuera
    _, hidden = follow(before, after)
    assert hidden == HiddenBoots.UPGRADED
    advice = bot_advice(plan, after, hidden)
    assert next(s for s in advice.build if s.item_id == BERSERKER).owned
    assert advice.next_purchase is not None and advice.next_purchase.item_id != BERSERKER
    # Sin seguirlo, el consejo era el del fallo: «compra ahora Grebas de berserker»
    wrong = bot_advice(plan, after, False)
    assert wrong.next_purchase is not None and wrong.next_purchase.item_id == BERSERKER


async def test_basic_boots_moved_to_the_slot_are_not_your_boots_yet() -> None:
    """Unas Botas en ese espacio no completan tus botas (lo que pasaba): falta mejorarlas.
    La mejora también se compra allí, sin verse: un gasto sin objeto nuevo la delata."""
    plan = await kaisa_plan()
    before, after = bot_game((1055, BASIC_BOOTS), gold=200), bot_game((1055,), gold=500)
    _, hidden = follow(before, after)
    assert hidden == HiddenBoots.BASIC
    advice = bot_advice(plan, bot_game((1055, KRAKEN), gold=500), hidden)
    assert not next(s for s in advice.build if s.item_id == BERSERKER).owned
    berserker = loaded_gamedata().item(BERSERKER)
    assert berserker is not None
    boots = bot_advice(plan, bot_game((1055,), gold=0), hidden).next_purchase
    if boots is not None and boots.item_id == BERSERKER:  # las Botas ya pagadas se descuentan
        assert boots.remaining_gold == berserker.price_total - 300
    # Luego mejora en ese espacio: el oro baja sin ningún objeto nuevo
    upgraded = bot_game((1055,), gold=500 - (berserker.price_total - 300))
    _, hidden = follow(before, after, upgraded)
    assert hidden == HiddenBoots.UPGRADED


async def test_magical_footwear_boots_are_not_your_boots() -> None:
    """El Calzado ligeramente mágico de la runa contaba como tus botas mejoradas."""
    plan = await kaisa_plan()
    gd = loaded_gamedata()
    footwear = gd.item(MAGICAL_FOOTWEAR)
    assert footwear is not None and footwear.is_boots and not footwear.is_upgraded_boots
    data = bot_game((1055, KRAKEN, MAGICAL_FOOTWEAR), gold=0)
    advice = bot_advice(plan, data, HiddenBoots.NONE)
    assert not next(s for s in advice.build if s.item_id == BERSERKER).owned
    # No es un «objeto empezado» que haya que terminar antes que el núcleo
    assert (
        advice.next_purchase is not None
        and advice.next_purchase.reason != "termina el objeto que has empezado"
    )
    boots = advise(plan, report_for(data, Position.BOTTOM)[1], (MAGICAL_FOOTWEAR,), 0, gd.item,
                   gd.champion_name, game_time=900)  # fmt: skip
    berserker = gd.item(BERSERKER)
    assert berserker is not None
    remaining = {s.item_id: s for s in boots.build}
    assert not remaining[BERSERKER].owned
    # Al comprarlas se mejora como unas Botas: su valor se descuenta
    from lolcoach.ingame.advisor import _purchase

    purchase = _purchase(BERSERKER, "", (MAGICAL_FOOTWEAR,), 0, gd.item)
    assert purchase.remaining_gold == berserker.price_total - 300
    assert BASIC_BOOTS not in purchase.affordable_components


async def test_basic_boots_in_the_inventory_are_not_your_boots() -> None:
    plan = await kaisa_plan()
    advice = bot_advice(plan, bot_game((1055, KRAKEN, BASIC_BOOTS), gold=0), HiddenBoots.NONE)
    assert not next(s for s in advice.build if s.item_id == BERSERKER).owned


async def test_boots_bought_straight_into_the_role_quest_slot() -> None:
    """Botas compradas después de la misión: el oro baja y no aparece ningún objeto."""
    _, hidden = follow(bot_game((1055, KRAKEN), gold=1200), bot_game((1055, KRAKEN), gold=100))
    assert hidden == HiddenBoots.UPGRADED  # 1100 de golpe: ya mejoradas
    _, cheap = follow(bot_game((1055, KRAKEN), gold=400), bot_game((1055, KRAKEN), gold=100))
    assert cheap == HiddenBoots.BASIC  # 300: unas Botas


def test_visible_purchases_are_not_mistaken_for_hidden_boots() -> None:
    _, bought_pickaxe = follow(bot_game((1055,), gold=1000), bot_game((1055, PICKAXE), gold=125))
    _, small_spend = follow(bot_game((1055,), gold=300), bot_game((1055,), gold=225))  # guardián
    _, nothing = follow(bot_game((1055,), gold=300), bot_game((1055,), gold=320))
    assert bought_pickaxe == small_spend == nothing == HiddenBoots.NONE


def test_the_role_quest_slot_is_only_for_the_bottom_lane() -> None:
    _, sold = follow(
        bot_game((1055, BERSERKER), gold=400, position="MIDDLE"),
        bot_game((1055,), gold=1170, position="MIDDLE"),  # en otra línea: las ha vendido
    )
    assert sold == HiddenBoots.NONE


async def test_complete_build() -> None:
    plan = await ahri_plan()
    advice = advice_for(plan, standard_game(my_items=plan.full))
    assert advice.next_purchase is None and "completa" in advice.message
    assert all(step.owned for step in advice.build)


def test_a_second_identical_item_is_not_mistaken_for_hidden_boots() -> None:
    """Dos Espadas largas en huecos distintos: la segunda explica el oro gastado."""
    long_sword = 1036
    _, hidden = follow(
        bot_game((1055, long_sword), gold=700), bot_game((1055, long_sword, long_sword), gold=350)
    )
    assert hidden == HiddenBoots.NONE


async def test_urgent_defense_does_not_flip_while_the_threat_is_still_strong() -> None:
    """Si Zed pasa de «muy fuerte» a «fuerte», el consejo de defensa se mantiene (antes
    alternaba con la build cada pocos segundos)."""
    plan = await ahri_plan()
    gd = loaded_gamedata()
    data = standard_game(
        zed_kda=(8, 1, 2), zed_items=FED_ZED_ITEMS, my_items=AHRI_CORE, game_time=AFTER_LANING
    )
    game, report = report_for(data)
    assert game.me is not None
    first = advise(
        plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name,
        game_time=game.game_time,
    )  # fmt: skip
    assert first.next_purchase is not None and first.next_purchase.item_id == ZHONYA
    zed = next(e for e in report.enemies if e.player.champion_alias == "Zed")
    assert first.threat_id == zed.champion_id
    calmer = dataclasses.replace(zed, strength=Strength.STRONG, is_lane_opponent=False)
    report = dataclasses.replace(
        report, enemies=tuple(calmer if e is zed else e for e in report.enemies)
    )

    def again(keep: int) -> int | None:
        assert game.me is not None
        advice = advise(
            plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name,
            game_time=game.game_time, keep_threat=keep,
        )  # fmt: skip
        return advice.next_purchase.item_id if advice.next_purchase else None

    assert again(first.threat_id) == ZHONYA  # sigue fuerte: se mantiene
    assert again(0) == MORELLO  # sin el aviso anterior, «fuerte» no basta (no es tu rival)


# --- Objetos que se transforman (mejoras sin comprar) ---------------------------------------
# El Bastón del arcángel se convierte en Abrazo del serafín al cargarse de maná: el juego
# solo enseña el nuevo, y antes Erizo Coach seguía pidiendo el Bastón.

ARCHANGEL, SERAPH, MANAMUNE, MURAMANA = 3003, 3040, 3004, 3042


async def test_an_upgraded_item_counts_as_its_base_item() -> None:
    plan = dataclasses.replace(
        await ahri_plan(),
        core=(ARCHANGEL, SHADOWFLAME, RABADON),
        full=(SORCERER, ARCHANGEL, SHADOWFLAME, RABADON, MORELLO, ZHONYA),
    )
    advice = advice_for(plan, standard_game(my_items=(SORCERER, SERAPH)))
    assert [s.owned for s in advice.build][:3] == [True, True, False]
    assert advice.next_purchase is not None and advice.next_purchase.item_id == SHADOWFLAME


def test_upgrade_chains_are_followed() -> None:
    from lolcoach.gamedata.items import with_upgraded

    gd = loaded_gamedata()
    assert gd.item(SERAPH).upgraded_from == ARCHANGEL  # type: ignore[union-attr]
    assert gd.item(MURAMANA).upgraded_from == MANAMUNE  # type: ignore[union-attr]
    assert with_upgraded((SERAPH, 1056), gd.item) == {SERAPH, ARCHANGEL, 1056}
    # Objeto de apoyo: Botín universal ← Brújula rúnica ← Atlas mundial
    assert with_upgraded((3867,), gd.item) == {3867, 3866, 3865}
    assert with_upgraded((ARCHANGEL,), gd.item) == {ARCHANGEL}  # el de base no es el mejorado


def test_a_broken_upgrade_loop_in_the_data_does_not_hang() -> None:
    from lolcoach.gamedata.items import Item, with_upgraded

    def fake(item_id: int) -> Item:  # 1 ← 2 ← 1: datos rotos
        return Item(item_id, "x", 0, (), (), frozenset(), False, False, "", "", "", 3 - item_id)

    assert with_upgraded((1,), fake) == {1, 2}


# --- Objetos iniciales --------------------------------------------------------------------

TRINKET = 3340


async def test_at_the_start_it_recommends_the_starting_items() -> None:
    plan = await ahri_plan()
    assert plan.starting
    gd = loaded_gamedata()
    total = sum(gd.item(s.item_id).price_total * s.count for s in plan.starting)  # type: ignore[union-attr]
    for items in ((), (TRINKET,)):  # el abalorio es gratis: no cuenta como compra
        advice = advice_for(plan, standard_game(my_items=items, gold=500, game_time=5))
        purchase = advice.next_purchase
        assert purchase is not None and purchase.starting == plan.starting
        assert purchase.item_id == plan.starting[0].item_id  # el principal, no las pociones
        assert "iniciales" in purchase.reason
        assert purchase.can_buy_now == (total <= 500)
        assert purchase.remaining_gold == max(total - 500, 0)


async def test_after_the_start_the_build_takes_over() -> None:
    plan = await ahri_plan()
    bought = advice_for(plan, standard_game(my_items=(plan.starting[0].item_id,), game_time=40))
    assert bought.next_purchase is not None and not bought.next_purchase.starting
    late = advice_for(plan, standard_game(my_items=(), game_time=600))  # lo ha vendido todo
    assert late.next_purchase is not None and not late.next_purchase.starting


# --- Oro frente al rival de posición --------------------------------------------------------


def test_each_player_is_compared_with_their_positional_rival() -> None:
    _, report = report_for(standard_game(zed_kda=(5, 1, 2), zed_items=FED_ZED_ITEMS))
    gd = loaded_gamedata()
    players = [report.me, *report.allies, *report.enemies]
    by_name = {r.player.champion_alias: r for r in players if r is not None}
    pairs = [("Garen", "Darius"), ("LeeSin", "Graves"), ("Ahri", "Zed"),
             ("Jinx", "Kaisa"), ("Thresh", "Soraka")]  # fmt: skip
    for ally, enemy in pairs:
        a, e = by_name[ally], by_name[enemy]
        assert a.lane_rival_id == e.champion_id and e.lane_rival_id == a.champion_id
        assert a.gold_vs_lane_rival == a.item_gold - e.item_gold
        assert e.gold_vs_lane_rival == -a.gold_vs_lane_rival  # type: ignore[operator]
    assert by_name["Zed"].gold_vs_lane_rival > 0  # type: ignore[operator]
    assert gd.champion_name(by_name["Ahri"].lane_rival_id) == "Zed"


def test_without_positions_from_the_game_they_are_deduced() -> None:
    """A ciegas o en la práctica el juego no da líneas: se deducen por campeón."""
    data = standard_game()
    for player in data["allPlayers"]:
        player["position"] = ""
    _, report = report_for(data, my_position=Position.NONE)
    by_name = {
        r.player.champion_alias: r for r in [report.me, *report.allies, *report.enemies] if r
    }
    assert by_name["Garen"].position == Position.TOP
    assert by_name["Jinx"].position == Position.BOTTOM
    assert by_name["Thresh"].lane_rival_id == by_name["Soraka"].champion_id


def test_other_modes_have_no_positional_rival() -> None:
    data = standard_game()
    data["gameData"]["mapNumber"] = 12  # ARAM: todos en la misma línea
    _, report = report_for(data)
    assert report.me is not None and report.me.gold_vs_lane_rival is None
