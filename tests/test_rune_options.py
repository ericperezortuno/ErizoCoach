"""Tres páginas de runas para elegir: las piedras angulares más jugadas en alto elo (o las
recomendaciones de Riot si faltan datos)."""

from __future__ import annotations

import dataclasses
import gzip
import json
from typing import Any

import pytest

from lolcoach.bus import EventBus
from lolcoach.champselect.parser import parse_session
from lolcoach.events import ChampSelectStarted, LoadoutPreview
from lolcoach.gamedata.runes import RunePage, validate_page
from lolcoach.loadout.models import StatsRuneOption
from lolcoach.loadout.recommender import LoadoutRecommender
from lolcoach.models import Position, QueueInfo
from lolcoach.stats.aggregate import (
    ChampionStats,
    aggregate,
    is_fresh,
    load_stats,
    save_stats,
    stats_payload,
)
from lolcoach.stats.extract import PlayerBuild
from lolcoach.stats.pack import PackError, decode_pack, encode_pack
from lolcoach.stats.source import StatsBuildSource
from lolcoach.ui.web.presenter import UiPresenter

from .factories import AHRI, LUX, DraftScenario
from .gamedata_fixture import FixtureClient, loaded_gamedata

SR = QueueInfo(420, "Clasificatoria", "CLASSIC", 11)
ELECTROCUTE = (8100, 8200, 8112, 8139, 8140, 8106, 8226, 8210, 5005, 5008, 5001)
COMET = (8200, 8100, 8229, 8226, 8210, 8237, 8139, 8106, 5008, 5008, 5001)
HARVEST = (8100, 8200, 8128, 8126, 8138, 8135, 8226, 8210, 5008, 5008, 5001)
FLEET = (8000, 8200, 8021, 9111, 9104, 8014, 8226, 8210, 5005, 5008, 5001)


def game(runes: tuple[int, ...], win: bool = True) -> PlayerBuild:
    return PlayerBuild(AHRI, "MIDDLE", win, (), (), runes, (4, 14))


def ahri_games() -> list[PlayerBuild]:
    """Electrocutar 8 (6 victorias), Cometa 6 (2), Cosecha 5 (5) y Pies veloces 2 (ruido)."""
    return (
        [game(ELECTROCUTE)] * 6
        + [game(ELECTROCUTE, False)] * 2
        + [game(COMET)] * 2
        + [game(COMET, False)] * 4
        + [game(HARVEST)] * 5
        + [game(FLEET)] * 2
    )


# --- Estadísticas ------------------------------------------------------------------------------


def test_the_three_most_played_keystones_are_kept_with_their_results() -> None:
    entry = aggregate(ahri_games(), loaded_gamedata().item)[(AHRI, "MIDDLE")]
    assert [o[:2] for o in entry.rune_options] == [(8, 6), (6, 2), (5, 5)]
    assert [tuple(o[2:]) for o in entry.rune_options] == [ELECTROCUTE, COMET, HARVEST]
    assert tuple(entry.rune_options[0][2:]) == entry.runes  # la 1.ª es la página de siempre


def test_a_rarely_played_keystone_is_not_an_option() -> None:
    games = [game(ELECTROCUTE)] * 20 + [game(COMET)] * 4  # 4 partidas: ruido
    entry = aggregate(games, loaded_gamedata().item)[(AHRI, "MIDDLE")]
    assert len(entry.rune_options) == 1


def entry_with_options() -> ChampionStats:
    return aggregate(ahri_games() * 2, loaded_gamedata().item)[(AHRI, "MIDDLE")]


def test_options_travel_in_the_shared_pack_and_the_local_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    entry = entry_with_options()
    original = ("16.22", 500, {(AHRI, "MIDDLE"): entry})
    assert decode_pack(encode_pack(original)) == original
    path = tmp_path / "builds.json"
    save_stats(path, "16.22", 500, original[2], 42)
    assert load_stats(path) == original and is_fresh(path, 500, 42)


def test_a_cache_without_options_is_recomputed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "builds.json"
    payload = stats_payload("16.22", 500, {(AHRI, "MIDDLE"): entry_with_options()}, 42)
    del payload["rune_options"]  # hecha por una versión anterior
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert not is_fresh(path, 500, 42)


def test_packs_from_older_versions_still_work() -> None:
    payload = stats_payload("16.22", 500, {(AHRI, "MIDDLE"): entry_with_options()})
    for build in payload["builds"].values():
        del build["rune_options"]
    _, _, kept = decode_pack(gzip.compress(json.dumps(payload).encode("utf-8")))
    assert kept[(AHRI, "MIDDLE")].rune_options == () and kept[(AHRI, "MIDDLE")].runes


def test_broken_options_drop_the_entry() -> None:
    def pack_with(options: list[Any]) -> dict[tuple[int, str], ChampionStats]:
        payload = stats_payload("16.22", 500, {(AHRI, "MIDDLE"): entry_with_options()})
        payload["builds"]["103:MIDDLE"]["rune_options"] = options
        return decode_pack(gzip.compress(json.dumps(payload).encode("utf-8")))[2]

    good = [16, 12, *ELECTROCUTE]
    assert pack_with([good])
    for bad in (
        [good] * 4,  # demasiadas
        [[16, 12, *ELECTROCUTE[:5]]],  # incompleta
        [[16, 17, *ELECTROCUTE]],  # más victorias que partidas
        [[999, 12, *ELECTROCUTE]],  # más partidas que las que tienen runas
        [[16, 12, *ELECTROCUTE[:-1], -5]],
    ):
        with pytest.raises(PackError):  # su única entrada se descarta: el paquete no sirve
            pack_with(bad)


def test_the_stats_source_gives_the_options() -> None:
    source = StatsBuildSource(min_games=20)
    entry = entry_with_options()
    source.update("16.22", 500, {(AHRI, "MIDDLE"): entry})
    options = source.rune_options_for(AHRI, Position.MIDDLE)
    assert [(o.games, o.wins) for o in options] == [(16, 12), (12, 4), (10, 10)]
    assert options[1].page == RunePage(8200, 8100, COMET[2:])
    few = dataclasses.replace(entry, loadout_games=10)  # pocas partidas con runas
    source.update("16.22", 500, {(AHRI, "MIDDLE"): few})
    assert source.rune_options_for(AHRI, Position.MIDDLE) == ()


# --- Recomendación -----------------------------------------------------------------------------


class Stats:
    """Alto elo de Ahri: Electrocutar, una de un parche viejo (no válida) y Cometa."""

    def loadout_for(self, champion_id: int, position: Position) -> None:
        return None  # la base sale de Riot: así se ve que la 1.ª opción es la aplicada

    def rune_options_for(self, champion_id: int, position: Position) -> tuple:
        if champion_id != AHRI:
            return ()
        stale = RunePage(8200, 8200, (8214,) * 9)
        return (
            StatsRuneOption(RunePage(8100, 8200, ELECTROCUTE[2:]), 80, 44),
            StatsRuneOption(stale, 40, 20),
            StatsRuneOption(RunePage(8200, 8100, COMET[2:]), 30, 12),
        )


def snapshot(champion: int) -> Any:
    return parse_session(DraftScenario().state(3, hover=champion)).with_my_champion(champion)


async def test_three_different_keystones_with_the_chosen_one_first() -> None:
    gd = loaded_gamedata()
    loadout = await LoadoutRecommender(gd, stats=Stats()).recommend(
        FixtureClient(), snapshot(AHRI), SR
    )
    assert loadout is not None and loadout.runes is not None
    options = loadout.options
    assert len(options) == 3 and options[0].recommended and options[0].page == loadout.runes
    assert not any(o.recommended for o in options[1:])
    keystones = [o.page.keystone_id for o in options]
    assert len(set(keystones)) == 3 and keystones[:2] == [8112, 8229]
    assert all(validate_page(o.page, gd.styles) == [] for o in options)
    # La 1.ª (Electrocutar, la de Riot) lleva los datos de alto elo de esa piedra angular
    assert options[0].detail == "Recomendada · 80 partidas de alto elo · 55 % de victorias"
    assert options[1].detail == "30 partidas de alto elo · 40 % de victorias"
    assert options[2].detail.startswith("Recomendación de Riot")


async def test_without_stats_the_options_come_from_riot() -> None:
    gd = loaded_gamedata()
    loadout = await LoadoutRecommender(gd).recommend(FixtureClient(), snapshot(LUX), SR)
    assert loadout is not None and loadout.options
    assert loadout.options[0].recommended and loadout.options[0].page == loadout.runes
    assert all(
        o.detail.startswith(("Recomendada", "Recomendación de Riot")) for o in loadout.options
    )
    keystones = [o.page.keystone_id for o in loadout.options]
    assert len(keystones) == len(set(keystones))


async def test_the_window_lists_the_options() -> None:
    bus, gd = EventBus(), loaded_gamedata()
    presenter = UiPresenter(bus, gd)
    loadout = await LoadoutRecommender(gd, stats=Stats()).recommend(
        FixtureClient(), snapshot(AHRI), SR
    )
    assert loadout is not None
    await bus.publish(ChampSelectStarted(snapshot(AHRI), None))
    await bus.publish(LoadoutPreview(AHRI, loadout))
    options = presenter.state["loadout"]["options"]
    assert len(options) == 3 and options[0]["recommended"]
    assert options[1]["page"]["keystone"]["id"] == 8229
    assert len(options[1]["page"]["perkIds"]) == 9 and "victorias" in options[1]["detail"]


async def test_the_recommended_page_says_where_it_comes_from() -> None:
    """Base de alto elo sin datos por piedra angular (paquete antiguo): no dice «de Riot»."""
    from lolcoach.loadout.models import StatsLoadout

    class OnlyBase(Stats):
        def loadout_for(self, champion_id: int, position: Position) -> StatsLoadout | None:
            page = RunePage(8100, 8200, ELECTROCUTE[2:])
            return StatsLoadout(page, (4, 14), 300, "16.22")

        def rune_options_for(self, champion_id: int, position: Position) -> tuple:
            return ()

    gd = loaded_gamedata()
    loadout = await LoadoutRecommender(gd, stats=OnlyBase()).recommend(
        FixtureClient(), snapshot(AHRI), SR
    )
    assert loadout is not None
    assert loadout.options[0].detail == "Recomendada · la más jugada en alto elo"
    riot = await LoadoutRecommender(gd).recommend(FixtureClient(), snapshot(AHRI), SR)
    assert riot is not None and riot.options[0].detail.startswith("Recomendada · de Riot")
