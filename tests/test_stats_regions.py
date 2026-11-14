"""Más datos: Diamante I y II, varias regiones a la vez y un único tope de partidas."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from lolcoach.bus import EventBus
from lolcoach.config import DEFAULT_STATS_REGIONS, Settings, load_settings
from lolcoach.events import StatsStatus
from lolcoach.models import ClientInfo
from lolcoach.stats.crawler import APEX_TIERS, Crawler, crawl_regions
from lolcoach.stats.extract import extract_builds
from lolcoach.stats.riot_api import RiotApiClient, distinct_routes, match_prefix
from lolcoach.stats.store import MatchStore, players_refreshed_key
from lolcoach.stats.updater import StatsUpdater

from .fake_riot_api import API_KEY, FakeRiotApi, build_match

INFO = ClientInfo("16.22.834.8910", "EUW1", "es_ES")


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def euw() -> AsyncIterator[FakeRiotApi]:
    api = FakeRiotApi("EUW1")
    await api.start()
    yield api
    await api.stop()


@pytest.fixture
async def kr() -> AsyncIterator[FakeRiotApi]:
    api = FakeRiotApi("KR")
    await api.start()
    yield api
    await api.stop()


def client(riot: FakeRiotApi, region: str = "euw1") -> RiotApiClient:
    return RiotApiClient(API_KEY, region, hosts=(riot.url, riot.url), sleep=no_sleep)


# --- Diamante I y II ------------------------------------------------------------------


async def test_diamond_is_read_page_by_page(euw: FakeRiotApi, tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    async with client(euw) as api:
        await Crawler(api, store, patch="16.22").run()
    pages = [r for r in euw.requests if "/entries/RANKED_SOLO_5x5/DIAMOND/I?" in r]
    assert pages == [
        f"/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I?page={n}" for n in (1, 2, 3)
    ]
    assert any("/DIAMOND/II?page=1" in r for r in euw.requests)  # Diamante II también
    assert not any("/DIAMOND/III" in r or "/EMERALD/" in r for r in euw.requests)  # y nada más
    tiers = dict(store._db.execute("SELECT player_id, tier FROM players").fetchall())
    assert tiers["diamante-0"] == tiers["diamante-1"] == "DIAMOND I"
    assert store.player_count("euw1") == 5
    store.close()


def test_master_and_above_are_crawled_before_diamond(tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    store.add_players([(f"d{n}", "DIAMOND II") for n in range(20)])
    store.add_players([(f"c{n}", tier) for n, tier in enumerate(APEX_TIERS)])
    order = store.players_to_crawl("euw1")
    assert set(order[:3]) == {"c0", "c1", "c2"}
    store.mark_crawled("c0")  # ya consultado: va después de los que nunca se han consultado
    assert store.players_to_crawl("euw1")[-1] == "c0"
    store.close()


async def test_leagues_cut_short_are_read_again(euw: FakeRiotApi, tmp_path: Path) -> None:
    """Si se acaba el tiempo a mitad de las páginas, la siguiente tanda las vuelve a leer."""
    store = MatchStore(tmp_path / "matches.sqlite3")
    async with client(euw) as api:
        await Crawler(api, store, patch="16.22", max_seconds=0).run()
    assert store.get_meta(players_refreshed_key("euw1")) is None
    store.close()


# --- Varias regiones -------------------------------------------------------------------


def test_regions_share_limits_per_match_server() -> None:
    assert distinct_routes(("euw1", "eun1", "kr", "na1")) == ("euw1", "kr", "na1")
    assert match_prefix("euw1") == "EUW1_" and match_prefix("kr") == "KR_"


async def test_regions_download_at_once(euw: FakeRiotApi, kr: FakeRiotApi, tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    async with client(euw, "euw1") as a, client(kr, "kr") as b:
        result = await crawl_regions([a, b], store, patch="16.22")
    assert {p.region: p.new_matches for p in result.progress} == {"euw1": 11, "kr": 11}
    assert store.matches_by_region("16.22") == {"EUW1": 11, "KR": 11}
    assert store.players_by_region() == {"euw1": 5, "kr": 5}
    assert result.out_of_players and not result.errors
    # Cada región pregunta a su servidor solo por sus jugadores
    assert not any("kr-" in r for r in euw.requests)
    assert not any("/EUW1_" in r for r in kr.requests)
    store.close()


async def test_one_target_for_all_regions(
    euw: FakeRiotApi, kr: FakeRiotApi, tmp_path: Path
) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    async with client(euw, "euw1") as a, client(kr, "kr") as b:
        result = await crawl_regions([a, b], store, patch="16.22", max_matches=6)
    # Las dos regiones van a la vez: cada una puede pasarse en la partida que tenía en curso
    assert 6 <= result.new_matches <= 7
    assert all(p.new_matches for p in result.progress)  # las dos aportan
    store.close()


async def test_each_region_completes_only_its_own_matches(euw: FakeRiotApi, tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    match, timeline = build_match("KR_0000", "16.22.834.8910")
    store.add_match("KR_0000", "16.22", extract_builds(match, timeline))
    store._db.execute("UPDATE matches SET detail = 1")  # como si fuera de la v0.5.0
    store._db.commit()
    async with client(euw) as api:
        await Crawler(api, store, patch="16.22", max_matches=0).run()
    assert not any("KR_0000" in r for r in euw.requests)  # no se pregunta a EUW por ella
    assert store.matches_without_loadout("16.22", prefix="KR_") == ["KR_0000"]
    store.close()


def test_players_saved_before_regions_are_from_euw(tmp_path: Path) -> None:
    path = tmp_path / "matches.sqlite3"
    with sqlite3.connect(path) as db:  # tabla de jugadores de la v0.14
        db.execute(
            "CREATE TABLE players (player_id TEXT PRIMARY KEY, tier TEXT NOT NULL, "
            "crawled_at INTEGER)"
        )
        db.execute("INSERT INTO players VALUES ('p1', 'MASTER', NULL)")
    db.close()
    store = MatchStore(path)
    assert store.players_by_region() == {"euw1": 1}
    assert store.players_to_crawl("euw1") == ["p1"] and store.players_to_crawl("kr") == []
    store.close()


def test_forgetting_players_is_per_region(tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    store.add_players([("e1", "MASTER")], "euw1")
    store.add_players([("k1", "MASTER")], "kr")
    store.set_meta(players_refreshed_key("kr"), "1")
    assert store.forget_players("kr") == 1
    assert store.players_by_region() == {"euw1": 1}
    assert store.get_meta(players_refreshed_key("kr")) is None
    store.close()


# --- Ajustes -----------------------------------------------------------------------------


def write_config(tmp_path: Path, text: str) -> None:
    (tmp_path / "config.toml").write_text(text, encoding="utf-8")


def test_default_regions_and_target(tmp_path: Path) -> None:
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.stats_regions == DEFAULT_STATS_REGIONS == ("euw1", "kr", "na1")
    assert settings.stats_target_matches == 20_000 and not warnings


def test_your_region_goes_first(tmp_path: Path) -> None:
    write_config(tmp_path, 'riot_region = "eun1"\n')
    settings, _ = load_settings(data_dir=tmp_path)
    assert settings.stats_regions == ("eun1", "kr", "na1")  # sin euw1: comparte servidor


def test_regions_from_the_config(tmp_path: Path) -> None:
    write_config(tmp_path, 'stats_regions = ["kr"]\n')
    assert load_settings(data_dir=tmp_path)[0].stats_regions == ("kr",)

    write_config(tmp_path, 'stats_regions = ["euw1", "eun1"]\n')
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.stats_regions == ("euw1",) and "comparten" in warnings[0]

    for bad in ('"euw1"', '["luna1"]', "[]"):
        write_config(tmp_path, f"stats_regions = {bad}\n")
        settings, warnings = load_settings(data_dir=tmp_path)
        assert settings.stats_regions == DEFAULT_STATS_REGIONS and warnings


# --- Descarga automática ------------------------------------------------------------------


class Refresher:
    async def refresh(self, patch: str) -> None:
        return None


async def test_app_downloads_every_region_with_one_client_each(
    euw: FakeRiotApi, kr: FakeRiotApi, tmp_path: Path
) -> None:
    bus = EventBus()
    statuses: list[StatsStatus] = []
    bus.subscribe(StatsStatus, statuses.append)
    created: list[str] = []
    fakes = {"euw1": euw, "kr": kr}

    def factory(key: str, region: str) -> RiotApiClient:
        created.append(region)
        return RiotApiClient(key, region, hosts=(fakes[region].url,) * 2, sleep=no_sleep)

    settings = Settings(data_dir=tmp_path, stats_target_matches=200, stats_regions=("euw1", "kr"))
    updater = StatsUpdater(
        bus, None, Refresher(), settings, key_loader=lambda: API_KEY, api_factory=factory,
        retry_seconds=0.01,
    )  # fmt: skip
    await updater._on_connect(None, INFO)
    assert updater.task is not None
    await asyncio.wait_for(updater.task, 10)
    assert created == ["euw1", "kr"]
    # Sin más jugadores en ninguna región: al día, con el reparto por regiones
    assert "al día" in statuses[-1].text and "11 EUW1 · 11 KR" in statuses[-1].text
