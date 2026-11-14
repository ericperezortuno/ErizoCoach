"""Recomendación de runas y hechizos con los datos reales del parche 16.22."""

from __future__ import annotations

import pytest

from lolcoach.champselect.models import ChampSelectSnapshot
from lolcoach.champselect.parser import parse_session
from lolcoach.gamedata.catalog import ChampionProfile, SummonerSpell
from lolcoach.gamedata.runes import RecommendedPage, RunePage, validate_page
from lolcoach.loadout import recommender as recommender_module
from lolcoach.loadout.models import Loadout, MatchContext, StatsLoadout, StatsRuneOption
from lolcoach.loadout.positions import infer_positions, lane_opponent, resolve_my_position
from lolcoach.loadout.recommender import LoadoutRecommender
from lolcoach.loadout.rules import (
    BARRIER,
    CLEANSE,
    EXHAUST,
    FLASH,
    IGNITE,
    SHARD_HEALTH,
    SHARD_TENACITY,
    adjust_spells,
    keep_flash_key,
)
from lolcoach.models import LANE_POSITIONS, Position, QueueInfo

from .factories import (
    AHRI,
    AKALI,
    AMUMU,
    ASHE,
    DARIUS,
    GAREN,
    GRAVES,
    JINX,
    KAISA,
    KATARINA,
    LEBLANC,
    LEE_SIN,
    LEONA,
    LUX,
    MALPHITE,
    MORGANA,
    NAUTILUS,
    THRESH,
    ZED,
    action,
    player,
    session,
)
from .gamedata_fixture import FixtureClient, fixture, loaded_gamedata

SR = QueueInfo(440, "Flex", "CLASSIC", 11)
ARAM = QueueInfo(450, "ARAM", "ARAM", 12)
ARENA = QueueInfo(1700, "Arena", "CHERRY", 30)
STANDARD_ENEMIES = [DARIUS, GRAVES, ZED, KAISA, NAUTILUS]


def game(
    champion: int, position: str, enemies: list[int], spells: tuple[int, int] = (4, 14)
) -> ChampSelectSnapshot:
    my_team = [player(0, champion, position=position, spells=spells)]
    their_team = [player(5 + i, c, team=2) for i, c in enumerate(enemies)]
    actions = [[action(1, 0, "pick", champion, completed=True)]] + [
        [action(10 + i, 5 + i, "pick", c, completed=True, ally=False)]
        for i, c in enumerate(enemies)
    ]
    return parse_session(
        session(my_team=my_team, their_team=their_team, actions=actions, local_cell=0,
                phase="FINALIZATION")
    )  # fmt: skip


async def recommend(snapshot: ChampSelectSnapshot, queue: QueueInfo = SR) -> Loadout:
    loadout = await LoadoutRecommender(loaded_gamedata()).recommend(
        FixtureClient(), snapshot, queue
    )
    assert loadout is not None
    return loadout


# --- Datos y validación ---------------------------------------------------------------


def test_every_recorded_riot_page_is_valid() -> None:
    gd = loaded_gamedata()
    pages = [p for group in fixture()["recommendedPages"].values() for p in group]
    assert len(pages) >= 25
    for raw in pages:
        page = RecommendedPage.from_json(raw)
        assert page is not None
        assert validate_page(page.page, gd.styles) == [], raw["keystone"]["name"]


def test_validation_rejects_broken_pages() -> None:
    gd = loaded_gamedata()
    base = RecommendedPage.from_json(fixture()["recommendedPages"]["103/MIDDLE/11"][0])
    assert base is not None
    page = base.page  # Electrocutar, Dominación + Brujería
    assert validate_page(page.with_perk(0, 8139), gd.styles)  # runa normal como piedra angular
    assert validate_page(page.with_perk(5, page.perk_ids[4]), gd.styles)  # secundarias misma fila
    assert validate_page(page.with_perk(8, 5005), gd.styles)  # fragmento de otra fila
    same_tree = RunePage(page.primary_style_id, page.primary_style_id, page.perk_ids)
    assert validate_page(same_tree, gd.styles)
    assert validate_page(RunePage(page.primary_style_id, page.sub_style_id, (8112,)), gd.styles)


def test_riot_page_with_perk_objects_or_ids_is_parsed() -> None:
    raw = fixture()["recommendedPages"]["103/MIDDLE/11"][0]
    as_objects = RecommendedPage.from_json(raw)
    as_ids = RecommendedPage.from_json(raw | {"perks": [p["id"] for p in raw["perks"]]})
    assert as_objects is not None and as_ids is not None
    assert as_objects.page == as_ids.page
    assert RecommendedPage.from_json(raw | {"perks": [{"name": "sin id"}] * 9}) is None


# --- Líneas de los enemigos --------------------------------------------------------------


def test_positions_of_a_standard_team() -> None:
    gd = loaded_gamedata()
    probabilities = infer_positions(STANDARD_ENEMIES, gd.positions_for)
    expected = {DARIUS: Position.TOP, GRAVES: Position.JUNGLE, ZED: Position.MIDDLE,
                KAISA: Position.BOTTOM, NAUTILUS: Position.UTILITY}  # fmt: skip
    for champion, position in expected.items():
        assert max(probabilities[champion], key=probabilities[champion].get) == position
    for champion in STANDARD_ENEMIES:  # cada fila es una distribución
        assert sum(probabilities[champion].values()) == pytest.approx(1)
    for position in LANE_POSITIONS:  # y cada línea tiene exactamente un campeón
        assert sum(probabilities[c][position] for c in STANDARD_ENEMIES) == pytest.approx(1)
    guess = lane_opponent(probabilities, Position.MIDDLE)
    assert guess is not None and guess.champion_id == ZED and guess.probability > 0.9


def test_positions_edge_cases() -> None:
    gd = loaded_gamedata()
    assert infer_positions([], gd.positions_for) == {}
    assert lane_opponent(infer_positions([ZED], gd.positions_for), Position.NONE) is None
    unknown = infer_positions([999_999], gd.positions_for)  # campeón sin datos: equiprobable
    assert unknown[999_999][Position.TOP] == pytest.approx(0.2)


# --- Hechizos ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spells", "current", "expected"),
    [
        ((4, 14), (14, 4), (14, 4)),  # tienes Destello en la F
        ((21, 4), (4, 7), (4, 21)),  # Riot lo pone en la F; tú lo usas en la D
        ((4, 14), (4, 7), (4, 14)),
        ((4, 14), (0, 0), (4, 14)),  # sin preferencia conocida: orden de Riot
        ((6, 12), (4, 14), (6, 12)),  # recomendación sin Destello
    ],
)
def test_keep_flash_key(
    spells: tuple[int, int], current: tuple[int, int], expected: tuple[int, int]
) -> None:
    assert keep_flash_key(spells, current) == expected


def test_spell_not_available_in_mode_is_not_used() -> None:
    gd = loaded_gamedata()

    class OnlyAramCleanse:
        def __getattr__(self, name: str) -> object:
            return getattr(gd, name)

        def spell(self, spell_id: int) -> SummonerSpell | None:
            if spell_id == CLEANSE:
                return SummonerSpell(CLEANSE, "Limpiar", frozenset({"ARAM"}))
            return gd.spell(spell_id)

    profiles = fixture()["champions"]
    enemies = tuple(ChampionProfile.from_json(profiles[str(c)]) for c in (LEONA, MORGANA, ASHE))
    ctx = MatchContext(JINX, Position.BOTTOM, 11, "CLASSIC", None, enemies)
    spells, reasons = adjust_spells((BARRIER, FLASH), ctx, OnlyAramCleanse())
    assert spells == (BARRIER, FLASH) and reasons == []


# --- Recomendaciones completas ---------------------------------------------------------------


async def test_mage_against_assassin_gets_health_shard() -> None:
    loadout = await recommend(game(AHRI, "middle", STANDARD_ENEMIES))
    assert loadout.position == Position.MIDDLE
    assert loadout.lane_guess is not None and loadout.lane_guess.champion_id == ZED
    assert loadout.runes is not None and loadout.base is not None
    assert loadout.runes.perk_ids[8] == SHARD_HEALTH
    assert loadout.runes.perk_ids[:8] == loadout.base.page.perk_ids[:8]  # el resto, como Riot
    assert loadout.spells == (FLASH, IGNITE)
    assert len(loadout.adjustments) == 1 and "Zed" in loadout.adjustments[0]


async def test_marksman_against_heavy_crowd_control() -> None:
    enemies = [LEONA, MORGANA, ASHE, MALPHITE, AMUMU]
    loadout = await recommend(game(JINX, "bottom", enemies, spells=(BARRIER, FLASH)))
    assert loadout.runes is not None and loadout.runes.perk_ids[8] == SHARD_TENACITY
    assert loadout.spells == (CLEANSE, FLASH)  # Limpiar y Destello en tu tecla (F)
    assert any("control" in a for a in loadout.adjustments)


async def test_support_against_divers_takes_exhaust() -> None:
    loadout = await recommend(game(THRESH, "utility", [ZED, AKALI, KATARINA, KAISA, GAREN]))
    assert loadout.spells is not None and EXHAUST in loadout.spells
    assert IGNITE not in loadout.spells
    assert loadout.notes  # nadie va de apoyo enemigo: rival de línea dudoso


async def test_immobile_mage_against_assassin_takes_barrier() -> None:
    loadout = await recommend(game(LUX, "middle", [GAREN, LEE_SIN, ZED, JINX, THRESH], (12, 4)))
    assert loadout.spells == (BARRIER, FLASH)


async def test_blind_pick_uses_defaults() -> None:
    loadout = await recommend(game(AHRI, "middle", []))
    assert loadout.base is not None and loadout.runes == loadout.base.page
    assert loadout.adjustments == ()
    assert loadout.notes and "por defecto" in loadout.notes[0]


async def test_no_assigned_position_uses_the_usual_one() -> None:
    loadout = await recommend(game(LEBLANC, "", []))
    assert loadout.position == Position.MIDDLE
    assert loadout.runes is not None


def test_position_hint_only_applies_without_assigned_position() -> None:
    positions_for = loaded_gamedata().positions_for

    def top(_champion: int) -> Position:
        return Position.TOP

    def unknown(_champion: int) -> Position:
        return Position.NONE

    assert resolve_my_position(Position.MIDDLE, LEBLANC, 11, positions_for, top) == Position.MIDDLE
    assert resolve_my_position(Position.NONE, LEBLANC, 11, positions_for, top) == Position.TOP
    assert (
        resolve_my_position(Position.NONE, LEBLANC, 11, positions_for, unknown) == Position.MIDDLE
    )
    assert resolve_my_position(Position.NONE, LEBLANC, 12, positions_for, top) == Position.NONE


async def test_runes_follow_the_position_hint() -> None:
    recommender = LoadoutRecommender(loaded_gamedata(), position_hint=lambda _c: Position.TOP)
    loadout = await recommender.recommend(FixtureClient(), game(LEBLANC, "", []), SR)
    assert loadout is not None and loadout.position == Position.TOP


class FakeStats:
    """Runas de alto elo para Ahri: Cometa (Brujería + Dominación) con Destello + Teleportar."""

    def __init__(self) -> None:
        self.asked: list[tuple[int, Position]] = []

    def loadout_for(self, champion_id: int, position: Position) -> StatsLoadout | None:
        self.asked.append((champion_id, position))
        if champion_id != AHRI:
            return None
        # Secundarias al revés de como las ordena el cliente: se deben reordenar por fila
        page = RunePage(8200, 8100, (8992, 8226, 8210, 8237, 8106, 8139, 5005, 5008, 5001))
        return StatsLoadout(page, (4, 12), 80, "16.22")

    def rune_options_for(self, champion_id: int, position: Position) -> tuple:
        if champion_id != AHRI:
            return ()
        comet = RunePage(8200, 8100, (8992, 8226, 8210, 8237, 8106, 8139, 5005, 5008, 5001))
        electrocute = RunePage(8100, 8200, (8112, 8139, 8140, 8106, 8226, 8210, 5005, 5008, 5001))
        stale = RunePage(8200, 8200, (8214,) * 9)  # de un parche anterior: se descarta
        return (
            StatsRuneOption(comet, 50, 27),
            StatsRuneOption(stale, 20, 10),
            StatsRuneOption(electrocute, 30, 14),
        )


async def test_stats_runes_and_spells_are_the_base_when_available() -> None:
    stats = FakeStats()
    recommender = LoadoutRecommender(loaded_gamedata(), stats=stats)
    loadout = await recommender.recommend(FixtureClient(), game(AHRI, "middle", []), SR)
    assert loadout is not None and stats.asked == [(AHRI, Position.MIDDLE)]
    assert loadout.runes is not None
    assert loadout.runes.perk_ids == (8992, 8226, 8210, 8237, 8139, 8106, 5005, 5008, 5001)
    assert loadout.spells == (4, 12)  # Destello sigue en tu tecla (D)
    assert loadout.source == "estadísticas de 80 partidas de alto elo (parche 16.22)"


async def test_riot_page_is_used_without_stats_or_with_invalid_stats() -> None:
    no_data = await recommend(game(LUX, "middle", []))
    assert no_data.source.startswith("recomendación de Riot")

    class BrokenStats:
        def loadout_for(self, _champion: int, _position: Position) -> StatsLoadout:
            page = RunePage(8200, 8200, (8992,) * 9)  # runas que ya no existen
            return StatsLoadout(page, (4, 12), 80, "16.21")

        def rune_options_for(self, _champion: int, _position: Position) -> tuple:
            return ()

    recommender = LoadoutRecommender(loaded_gamedata(), stats=BrokenStats())
    loadout = await recommender.recommend(FixtureClient(), game(AHRI, "middle", []), SR)
    assert loadout is not None and loadout.source.startswith("recomendación de Riot")
    assert loadout.base is not None and loadout.runes == loadout.base.page


async def test_aram_ignores_stats() -> None:
    stats = FakeStats()
    recommender = LoadoutRecommender(loaded_gamedata(), stats=stats)
    await recommender.recommend(FixtureClient(), game(AHRI, "", []), ARAM)
    assert stats.asked == []  # las estadísticas son de clasificatorias en la Grieta


async def test_aram_uses_aram_recommendation() -> None:
    loadout = await recommend(game(AHRI, "", [GAREN, LEE_SIN, ZED, JINX, THRESH]), ARAM)
    assert loadout.position == Position.NONE
    assert loadout.spells == (FLASH, 32)  # Destello + Marca
    assert loadout.lane_guess is None


async def test_mode_without_runes_changes_nothing() -> None:
    loadout = await recommend(game(GAREN, "", []), ARENA)
    assert loadout.runes is None and loadout.spells is None
    assert loadout.notes


async def test_broken_adjustment_falls_back_to_riot(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(page: RunePage, *_: object) -> tuple[RunePage, list[str]]:
        return page.with_perk(0, 5008), ["ajuste roto"]

    monkeypatch.setattr(recommender_module, "adjust_runes", broken)
    loadout = await recommend(game(AHRI, "middle", STANDARD_ENEMIES))
    assert loadout.base is not None and loadout.runes == loadout.base.page
    assert "ajuste roto" not in loadout.adjustments


async def test_data_is_cached_between_calls() -> None:
    gd = loaded_gamedata()
    client = FixtureClient()
    rec = LoadoutRecommender(gd)
    snapshot = game(AHRI, "middle", STANDARD_ENEMIES)
    await rec.recommend(client, snapshot, SR)
    first = len(client.requests)
    await rec.recommend(client, snapshot, SR)
    assert len(client.requests) == first  # la segunda vez, todo de la caché
