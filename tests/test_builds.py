"""Builds: clasificación, adaptación a los enemigos y conjunto de objetos (datos del 16.22)."""

from __future__ import annotations

import pytest

from lolcoach.builds import archetypes, rules
from lolcoach.builds.archetypes import (
    ARCHETYPE_ITEMS,
    ARCHETYPE_OVERRIDES,
    ITEM_OVERRIDES,
    MERCURY,
    PLATED,
    SUPPORT_UPGRADES,
    RuleBasedSource,
    classify,
    starting_items,
)
from lolcoach.builds.itemset import build_item_set, is_ours, set_title
from lolcoach.builds.models import Archetype, BaseBuild, BuildPlan, ItemCount
from lolcoach.builds.recommender import BuildRecommender
from lolcoach.champselect.models import ChampSelectSnapshot
from lolcoach.champselect.parser import parse_session
from lolcoach.models import LANE_POSITIONS, Position, QueueInfo

from .factories import action, player, session
from .gamedata_fixture import FixtureClient, loaded_gamedata

SR = QueueInfo(440, "Flex", "CLASSIC", 11)
ARAM = QueueInfo(450, "ARAM", "ARAM", 12)
ARENA = QueueInfo(1700, "Arena", "CHERRY", 30)

# Campeones (id)
AHRI, LEBLANC, JINX, SMOLDER, KAISA, VAYNE, ZED, DARIUS, MALPHITE = (
    103,
    7,
    222,
    901,
    145,
    67,
    238,
    122,
    54,
)
THRESH, LULU, LUX, BRAND, CASSIO, MORDE, LEE_SIN, GAREN = 412, 117, 99, 63, 69, 82, 64, 86
SORAKA, AATROX, VLADIMIR, MALZAHAR, ORNN, AMUMU, LEONA, NAUTILUS = 16, 266, 8, 90, 516, 32, 89, 111
GRAVES, YASUO, JANNA, KARMA, AKALI, KATARINA = 104, 157, 40, 43, 84, 55

STANDARD = [DARIUS, GRAVES, ZED, KAISA, NAUTILUS]
HEALERS = [SORAKA, AATROX, VLADIMIR, JINX, THRESH]
AP_TEAM = [AHRI, LUX, BRAND, VLADIMIR, MALZAHAR]
AD_TEAM = [DARIUS, ZED, JINX, GRAVES, YASUO]


def game(champion: int, position: str, enemies: list[int]) -> ChampSelectSnapshot:
    actions = [[action(1, 0, "pick", champion, completed=True)]] + [
        [action(10 + i, 5 + i, "pick", c, completed=True, ally=False)]
        for i, c in enumerate(enemies)
    ]
    return parse_session(
        session(
            my_team=[player(0, champion, position=position)],
            their_team=[player(5 + i, c, team=2) for i, c in enumerate(enemies)],
            actions=actions,
            local_cell=0,
            phase="FINALIZATION",
        )
    )


async def recommend(champion: int, position: str, enemies: list[int], queue=SR) -> BuildPlan:
    plan = await BuildRecommender(loaded_gamedata()).recommend(
        FixtureClient(), game(champion, position, enemies), queue
    )
    assert plan is not None
    return plan


# --- Datos: todo lo que la app recomienda existe en este parche -------------------------


def test_every_curated_item_is_buyable_in_this_patch() -> None:
    gd = loaded_gamedata()
    curated: set[int] = set()
    for boots, core, extra in ARCHETYPE_ITEMS.values():
        curated.update({*core, *extra} | ({boots} if boots else set()))
    for override in ITEM_OVERRIDES.values():
        for key in ("core", "extra"):
            curated.update(override.get(key, ()))  # type: ignore[arg-type]
        if override.get("boots"):
            curated.add(override["boots"])  # type: ignore[arg-type]
    for archetype in Archetype:
        for position in (*LANE_POSITIONS, Position.NONE):
            for map_id in (11, 12):
                curated.update(i.item_id for i in starting_items(archetype, position, map_id))
    not_buyable = sorted(i for i in curated if not gd.is_buyable(i))
    assert not_buyable == []
    for upgrade in (*SUPPORT_UPGRADES.values(), archetypes.MAGE_SUPPORT_UPGRADE):
        assert gd.item(upgrade) is not None


def test_every_champion_name_in_the_rules_exists() -> None:
    """Evita erratas: todas las claves internas usadas en las reglas son de campeones reales."""
    gd = loaded_gamedata()
    names = (set(ARCHETYPE_OVERRIDES) | set(ITEM_OVERRIDES) | rules.HEALERS | rules.SUPPRESSORS
             | rules.SHIELDERS | rules.STRONG_HEALERS)  # fmt: skip
    missing = sorted(n for n in names if not gd.champion_id_by_alias(n))
    assert missing == []


# --- Clasificación ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("champion", "position", "expected"),
    [
        (AHRI, Position.MIDDLE, Archetype.MAGE_BURST),
        (BRAND, Position.MIDDLE, Archetype.MAGE_SUSTAINED),
        (JINX, Position.BOTTOM, Archetype.MARKSMAN_CRIT),
        (SMOLDER, Position.BOTTOM, Archetype.MARKSMAN_CRIT),  # "marksman, mage": cuenta el 1.º
        (VAYNE, Position.BOTTOM, Archetype.MARKSMAN_ONHIT),
        (KAISA, Position.BOTTOM, Archetype.MARKSMAN_ONHIT),
        (ZED, Position.MIDDLE, Archetype.ASSASSIN_AD),
        (DARIUS, Position.TOP, Archetype.FIGHTER_AD),
        (MALPHITE, Position.TOP, Archetype.TANK),
        (MORDE, Position.TOP, Archetype.AP_FIGHTER),
        (THRESH, Position.UTILITY, Archetype.SUPPORT_TANK),
        (LULU, Position.UTILITY, Archetype.SUPPORT_ENCHANTER),
        (LUX, Position.UTILITY, Archetype.MAGE_BURST),  # apoyo mago: build de mago
        (BRAND, Position.UTILITY, Archetype.MAGE_SUSTAINED),
    ],
)
def test_classification(champion: int, position: Position, expected: Archetype) -> None:
    gd = loaded_gamedata()
    profile = gd._profiles.get(champion)  # perfiles grabados en el fixture
    assert classify(gd.champion(champion), profile, position) == expected


def test_classification_without_profile_uses_client_roles() -> None:
    gd = loaded_gamedata()
    assert classify(gd.champion(AHRI), None, Position.MIDDLE) == Archetype.MAGE_BURST
    assert classify(None, None, Position.MIDDLE) == Archetype.FIGHTER_AD


# --- Builds completas -------------------------------------------------------------------


async def test_build_shape() -> None:
    plan = await recommend(LEBLANC, "middle", STANDARD)
    assert plan.archetype == Archetype.MAGE_BURST and plan.source == "reglas"
    assert len(plan.full) == 6 and len(set(plan.full)) == 6
    assert plan.full[0] == plan.boots  # primero las botas
    assert plan.full[1:4] == plan.core  # después el núcleo, en orden
    assert [s.item_id for s in plan.starting] == [1056, 2003]  # Anillo de Doran + pociones


async def test_anti_heal_for_a_mage() -> None:
    plan = await recommend(AHRI, "middle", HEALERS)
    assert 3165 in plan.full  # Morellonomicón
    need = next(s for s in plan.situational if s.item_id == 3165)
    assert need.component_id == 3916  # Orbe del olvido, para comprarlo pronto
    assert "Soraka" in need.reason and "se curan" in need.reason


async def test_tank_does_not_buy_armor_anti_heal_against_mages() -> None:
    plan = await recommend(THRESH, "utility", AP_TEAM)  # el único que se cura es Vladimir (AP)
    assert 3075 not in [s.item_id for s in plan.situational]  # Malla de espinas
    assert plan.boots == MERCURY


async def test_marksman_against_suppression_and_magic() -> None:
    plan = await recommend(JINX, "bottom", AP_TEAM)
    assert 3139 in plan.full  # Cimitarra mercurial
    reason = next(s.reason for s in plan.situational if s.item_id == 3139)
    assert "Malzahar te puede suprimir" in reason


async def test_frontline_boots_adapt_to_enemy_damage() -> None:
    against_ap = await recommend(DARIUS, "top", AP_TEAM)
    assert against_ap.boots == MERCURY and 3065 in against_ap.full  # Rostro espiritual
    against_ad = await recommend(MALPHITE, "top", AD_TEAM)
    assert against_ad.boots == PLATED and 3143 in against_ad.full  # Presagio de Randuin


async def test_carries_keep_their_damage_boots() -> None:
    plan = await recommend(AHRI, "middle", AD_TEAM)
    assert plan.boots == 3020  # Botas de hechicero
    assert 3157 in plan.full  # Zhonya contra tanto daño físico


async def test_marksman_against_tanks_gets_armor_penetration() -> None:
    plan = await recommend(SMOLDER, "bottom", [MALPHITE, ORNN, ZED, GRAVES, NAUTILUS])
    assert plan.core == (3508, 6675, 3031)  # Segador de esencia, Navori, Filo infinito
    assert 3036 in [s.item_id for s in plan.situational]  # Recuerdos de lord Dominik


async def test_assassin_against_shields() -> None:
    plan = await recommend(ZED, "middle", [LULU, JANNA, KARMA, DARIUS, JINX])
    assert 6695 in plan.full  # Colmillo de serpiente


async def test_blind_pick_uses_base_build() -> None:
    plan = await recommend(VAYNE, "bottom", [])
    assert plan.situational == () and plan.adjustments == ()
    assert plan.core == (3153, 3124, 3091)
    assert plan.notes and "no visibles" in plan.notes[0]


async def test_champion_without_boots() -> None:
    plan = await recommend(CASSIO, "middle", STANDARD)
    assert plan.boots is None
    assert len(plan.full) == 6 and not any(loaded_gamedata().item(i).is_boots for i in plan.full)


@pytest.mark.parametrize("champion", [157, 777])  # Yasuo, Yone
async def test_crit_swordsmen_get_their_crit_build(champion: int) -> None:
    """Sin estadísticas, las reglas les daban Fuerza de trinidad (luchador); en alto elo van
    Arcoescudo inmortal, Filo infinito y Baile de la muerte con Grebas de berserker."""
    plan = await BuildRecommender(loaded_gamedata()).recommend_for(
        FixtureClient(), champion, Position.MIDDLE, [], 11
    )
    assert plan is not None
    assert plan.core == (6673, 3031, 6333) and plan.boots == 3006


async def test_support_mage_gets_support_start_and_upgrade() -> None:
    plan = await recommend(LUX, "utility", STANDARD)
    assert [s.item_id for s in plan.starting] == [3865, 2003]  # Atlas mundial + pociones
    assert plan.support_upgrade == archetypes.MAGE_SUPPORT_UPGRADE


async def test_jungle_start() -> None:
    plan = await recommend(LEE_SIN, "jungle", STANDARD)
    assert plan.starting[0].item_id == archetypes.SCORCHCLAW


async def test_aram_uses_guardian_item_and_no_support_upgrade() -> None:
    plan = await recommend(AHRI, "", STANDARD, ARAM)
    assert plan.starting == (ItemCount(archetypes.GUARDIAN_ORB),)
    assert plan.map_id == 12 and plan.support_upgrade is None


async def test_arena_has_no_build() -> None:
    gd = loaded_gamedata()
    assert await BuildRecommender(gd).recommend(FixtureClient(), game(AHRI, "", []), ARENA) is None


async def test_item_removed_from_the_patch_is_replaced() -> None:
    class SourceWithRemovedItem(RuleBasedSource):
        def base_build(self, *args: object) -> BaseBuild:
            base = super().base_build(*args)  # type: ignore[arg-type]
            return BaseBuild(base.archetype, base.source, base.starting, base.boots,
                             (999_999, *base.core[1:]), base.extra)  # fmt: skip

    gd = loaded_gamedata()
    plan = await BuildRecommender(gd, SourceWithRemovedItem()).recommend(
        FixtureClient(), game(AHRI, "middle", []), SR
    )
    assert plan is not None and 999_999 not in plan.full
    assert len(plan.core) == 3 and all(gd.is_buyable(i) for i in plan.full)


# --- Conjunto de objetos -------------------------------------------------------------------


async def test_item_set_format() -> None:
    plan = await recommend(AHRI, "middle", HEALERS)
    item_set = build_item_set(plan, "uid-1", set_title("Ahri"))
    assert item_set["title"] == "Erizo Coach: Ahri" and is_ours(item_set)
    assert item_set["associatedChampions"] == [AHRI] and item_set["associatedMaps"] == [11]
    titles = [b["type"] for b in item_set["blocks"]]
    assert titles == [
        "Inicio",
        "Botas",
        "Núcleo (en orden)",
        "Contra este equipo",
        "Build completa",
    ]
    first = item_set["blocks"][0]["items"]
    assert first == [{"id": "1056", "count": 1}, {"id": "2003", "count": 2}]
    situational = [i["id"] for i in item_set["blocks"][3]["items"]]
    assert situational.index("3916") < situational.index("3165")  # componente antes del objeto


def test_titles_are_truncated_to_client_limit() -> None:
    assert len(set_title("Nunu y Willump")) <= 25
    assert not is_ours({"title": "Mi conjunto", "type": "custom"})
