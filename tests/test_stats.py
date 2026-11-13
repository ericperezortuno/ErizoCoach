"""Fase 5: clave, API de Riot, descarga, privacidad, agregación y fuente de builds."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

from lolcoach.builds.models import Archetype, ItemCount
from lolcoach.builds.recommender import BuildRecommender
from lolcoach.logs import REDACTOR
from lolcoach.models import Position
from lolcoach.stats import keystore
from lolcoach.stats.aggregate import ChampionStats, aggregate, load_stats, save_stats
from lolcoach.stats.crawler import Crawler
from lolcoach.stats.extract import PlayerBuild, extract_builds, extract_loadout, patch_of
from lolcoach.stats.riot_api import (
    RateLimiter,
    RiotApiClient,
    RiotApiError,
    RiotApiKeyError,
    parse_limits,
)
from lolcoach.stats.service import prepare_stats
from lolcoach.stats.source import StatsBuildSource
from lolcoach.stats.store import MatchStore

from .fake_riot_api import AHRI_RUNES, API_KEY, FakeRiotApi, build_match
from .gamedata_fixture import FixtureClient, loaded_gamedata

AHRI, ZED = 103, 238


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def riot() -> AsyncIterator[FakeRiotApi]:
    api = FakeRiotApi()
    await api.start()
    yield api
    await api.stop()


def client(riot: FakeRiotApi, key: str = API_KEY) -> RiotApiClient:
    return RiotApiClient(key, hosts=(riot.url, riot.url), sleep=no_sleep)


# --- Clave ----------------------------------------------------------------------------


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.data:
            raise keyring.errors.PasswordDeleteError("no existe")
        del self.data[(service, username)]


def test_key_is_validated_stored_and_redacted() -> None:
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        assert not keystore.is_valid_format("hola")
        with pytest.raises(keystore.KeyStoreError):
            keystore.save_key("RGAPI-mala")
        keystore.save_key(f"  {API_KEY}\n")
        assert backend.data[("LoLCoach", "riot_api_key")] == API_KEY
        assert keystore.load_key() == API_KEY
        assert API_KEY in REDACTOR._secrets  # nunca aparecerá en los registros
        assert keystore.delete_key() and keystore.load_key() is None
        assert not keystore.delete_key()
    finally:
        keyring.set_keyring(previous)


# --- Límites de peticiones ---------------------------------------------------------------


def test_parse_limits() -> None:
    assert parse_limits("20:1,100:120") == ((20, 1.0), (100, 120.0))
    assert parse_limits(None) is None and parse_limits("x:y") is None


async def test_rate_limiter_never_exceeds_the_limits() -> None:
    now = [0.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(((2, 1.0), (3, 10.0)), clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(4):
        await limiter.acquire()
    # 2 por segundo -> la 3.ª espera ~1 s; 3 cada 10 s -> la 4.ª espera hasta el segundo 10
    assert len(waits) == 2
    assert waits[0] == pytest.approx(1.05) and now[0] >= 10


# --- Cliente de la API ----------------------------------------------------------------------


async def test_rate_limiter_remembers_requests_when_limits_change() -> None:
    now = [0.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(((100, 120.0), (20, 1.0)), clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(100):
        await limiter.acquire()
        now[0] += 0.06
    limiter.set_limits(((20, 1.0), (100, 120.0)))  # Riot los da en otro orden
    await limiter.acquire()
    assert waits and sum(waits) > 100  # la petición 101 espera a que pasen los 2 minutos


async def test_key_goes_in_a_header_never_in_the_url(riot: FakeRiotApi) -> None:
    async with client(riot) as api:
        await api.platform_status()
    assert riot.headers_seen[-1]["X-Riot-Token"] == API_KEY
    assert all(API_KEY not in path for path in riot.requests)


async def test_expired_key_is_reported_with_riots_reason(riot: FakeRiotApi) -> None:
    riot.expired = True
    async with client(riot) as api:
        with pytest.raises(RiotApiKeyError, match="24 h") as caught:
            await api.platform_status()
    assert caught.value.status == 403 and caught.value.riot_message == "Forbidden"
    assert API_KEY not in str(caught.value)


async def test_rate_limit_answer_is_retried(riot: FakeRiotApi) -> None:
    riot.rate_limited_once = True
    async with client(riot) as api:
        match = await api.match("EUW1_0000")
    assert match["metadata"]["matchId"] == "EUW1_0000"
    assert sum("/matches/EUW1_0000" in p and "timeline" not in p for p in riot.requests) == 2


async def test_only_official_hosts_are_allowed(riot: FakeRiotApi) -> None:
    async with client(riot) as api:
        with pytest.raises(RiotApiError, match="no permitido"):
            await api._get("https://evil.example", "/lol/status/v4/platform-data")


def test_unknown_region_is_rejected() -> None:
    with pytest.raises(ValueError):
        RiotApiClient(API_KEY, "luna1")


# --- Extracción y privacidad -------------------------------------------------------------


def test_extract_builds_keeps_order_and_undoes_cancelled_purchases() -> None:
    match, timeline = build_match("EUW1_X", "16.22.834.8910")
    builds = extract_builds(match, timeline)
    assert len(builds) == 10
    ahri = next(b for b in builds if b.champion_id == AHRI)
    assert ahri.position == "MIDDLE" and ahri.win
    items = [i for _, i in ahri.purchases]
    assert items[:3] == [1056, 2003, 2003] and 3157 not in items  # Zhonya deshecho
    assert patch_of("16.22.834.8910") == "16.22"


def test_runes_and_spells_are_extracted_in_client_order() -> None:
    match, timeline = build_match("EUW1_1", "16.22.834.8910")
    ahri = next(b for b in extract_builds(match, timeline) if b.champion_id == AHRI)
    assert ahri.runes == AHRI_RUNES and ahri.spells == (4, 14) and ahri.has_loadout
    assert PlayerBuild.from_json(ahri.to_json()) == ahri


def test_broken_runes_are_ignored() -> None:
    participant = build_match("EUW1_1", "16.22.834.8910")[0]["info"]["participants"][2]
    broken = participant | {"perks": {"styles": [], "statPerks": {}}, "summoner2Id": 0}
    assert extract_loadout(broken) == ((), ())
    old = PlayerBuild(AHRI, "MIDDLE", True, ((5, 1056),))  # guardada con la v0.5.0
    assert "r" not in old.to_json() and not PlayerBuild.from_json(old.to_json()).has_loadout


def test_short_games_are_ignored() -> None:
    match, timeline = build_match("EUW1_X", "16.22.834.8910", duration=300)
    assert extract_builds(match, timeline) == []


def test_extracted_builds_contain_no_personal_data() -> None:
    match, timeline = build_match("EUW1_X", "16.22.834.8910")
    text = repr([b.to_json() for b in extract_builds(match, timeline)])
    assert "puuid" not in text and "Jugador" not in text


# --- Descarga -------------------------------------------------------------------------------


async def test_crawler_downloads_current_patch_once(riot: FakeRiotApi, tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    async with client(riot) as api:
        progress = await Crawler(api, store, patch="16.22", tiers=("CHALLENGER",)).run()
    # 3 jugadores x 5 partidas que se solapan = 11 partidas distintas del parche
    assert progress.new_matches == 11 and store.match_count("16.22") == 11
    assert progress.skipped >= 2  # la partida corta y las de un parche anterior
    assert not any(
        "EUW1_OLD" in p and "timeline" in p for p in riot.requests
    )  # sin gastar peticiones

    requests = len(riot.requests)
    async with client(riot) as api:
        again = await Crawler(api, store, patch="16.22", tiers=("CHALLENGER",)).run()
    assert again.new_matches == 0  # nada se descarga dos veces
    assert len(riot.requests) - requests <= 3  # solo las listas de partidas de cada jugador

    database = (tmp_path / "matches.sqlite3").read_bytes()
    assert b"puuid-secreto" not in database and b"Jugador" not in database
    store.close()


async def test_old_matches_are_completed_with_runes(riot: FakeRiotApi, tmp_path: Path) -> None:
    """Partidas descargadas con la v0.5.0 (sin runas): se completan pidiendo solo el detalle."""
    path = tmp_path / "matches.sqlite3"
    match, timeline = build_match("EUW1_0000", "16.22.834.8910")
    old_builds = [PlayerBuild(b.champion_id, b.position, b.win, b.purchases, b.final_items)
                  for b in extract_builds(match, timeline)]  # fmt: skip
    with sqlite3.connect(path) as db:  # base de datos con el formato de la v0.5.0
        db.execute("CREATE TABLE matches (match_id TEXT PRIMARY KEY, patch TEXT NOT NULL, "
                   "collected_at INTEGER NOT NULL, builds TEXT)")  # fmt: skip
        payload = json.dumps([b.to_json() for b in old_builds])
        db.execute("INSERT INTO matches VALUES ('EUW1_0000', '16.22', 0, ?)", (payload,))
    db.close()

    store = MatchStore(path)
    assert store.matches_without_loadout("16.22") == ["EUW1_0000"]
    assert store.loadout_count("16.22") == 0
    async with client(riot) as api:
        progress = await Crawler(api, store, patch="16.22", max_matches=0).run()
    assert progress.completed == 1
    assert not any("EUW1_0000/timeline" in r for r in riot.requests)  # sin gastar de más
    assert store.matches_without_loadout("16.22") == [] and store.loadout_count("16.22") == 1
    ahri = next(b for b in store.match_builds("EUW1_0000") if b.champion_id == AHRI)
    assert ahri.runes == AHRI_RUNES and ahri.purchases == old_builds[2].purchases
    store.close()


async def test_backfill_keeps_matches_pending_without_connection(
    riot: FakeRiotApi, tmp_path: Path
) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    match, timeline = build_match("EUW1_0000", "16.22.834.8910")
    store.add_match("EUW1_0000", "16.22", extract_builds(match, timeline))
    store._db.execute("UPDATE matches SET detail = 1")  # como si fuera de la v0.5.0
    store._db.commit()
    riot.unavailable = True  # Riot caído
    async with client(riot) as api:
        with pytest.raises(RiotApiError):
            await Crawler(api, store, patch="16.22", max_matches=0).run()
    assert store.matches_without_loadout("16.22") == ["EUW1_0000"]  # se reintentará
    store.close()


def test_prune_keeps_recent_patches(tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    for patch in ("16.20", "16.21", "16.22"):
        match, timeline = build_match(f"EUW1_{patch}", f"{patch}.1.1")
        store.add_match(f"EUW1_{patch}", patch, extract_builds(match, timeline))
    assert store.prune(keep_from="16.21") == 1
    assert [p for p, _ in store.patches()] == ["16.22", "16.21"]
    store.close()


async def test_crawler_detects_the_patch_and_respects_limits(
    riot: FakeRiotApi, tmp_path: Path
) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    async with client(riot) as api:
        progress = await Crawler(api, store, max_matches=3, tiers=("CHALLENGER",)).run()
    assert progress.patch == "16.22" and progress.new_matches == 3
    assert "3 partidas" in progress.stopped_reason
    store.close()


async def test_crawler_stops_when_the_key_expires(riot: FakeRiotApi, tmp_path: Path) -> None:
    store = MatchStore(tmp_path / "matches.sqlite3")
    riot.expired = True
    async with client(riot) as api:
        with pytest.raises(RiotApiKeyError):
            await Crawler(api, store, patch="16.22", tiers=("CHALLENGER",)).run()
    store.close()


# --- Agregación ------------------------------------------------------------------------------


def sample_builds(games: int = 6) -> list:
    builds = []
    for n in range(games):
        match, timeline = build_match(f"EUW1_{n}", "16.22.834.8910", ahri_wins=n % 3 != 0)
        builds.extend(extract_builds(match, timeline))
    return builds


def test_aggregate_finds_the_real_build() -> None:
    gd = loaded_gamedata()
    stats = aggregate(sample_builds(), gd.item)
    ahri = stats[(AHRI, "MIDDLE")]
    assert ahri.games == 6 and ahri.wins == 4
    assert ahri.starting == (ItemCount(1056), ItemCount(2003, 2))  # Anillo de Doran + 2 pociones
    assert ahri.boots == 3020 and ahri.boots_rate == 1.0
    assert ahri.core == (6655, 4645, 3089)  # Luden, Llamasombría, Rabadon (en ese orden)
    assert 3135 in ahri.extra and 3157 not in ahri.extra and 3802 not in ahri.core
    zed = stats[(ZED, "MIDDLE")]
    assert zed.core == (3142, 6697, 6694) and zed.boots == 3158


def test_aggregate_finds_the_real_runes_and_spells() -> None:
    stats = aggregate(sample_builds(), loaded_gamedata().item)
    ahri = stats[(AHRI, "MIDDLE")]
    assert ahri.loadout_games == 6 and ahri.runes == AHRI_RUNES and ahri.spells == (4, 14)


def test_rune_page_follows_the_most_played_keystone() -> None:
    def game(runes: tuple[int, ...], spells: tuple[int, int]) -> PlayerBuild:
        return PlayerBuild(AHRI, "MIDDLE", True, (), (), runes, spells)

    comet = (8200, 8100, 8229, 8226, 8210, 8237, 8139, 8106, 5008, 5008, 5001)
    electrocute = AHRI_RUNES
    variant = (*AHRI_RUNES[:3], 8126, 8140, 8106, 8226, 8210, 5005, 5008, 5011)
    games = [game(electrocute, (4, 14))] * 3 + [game(variant, (14, 4))] * 2
    games += [game(comet, (4, 12))] * 4  # Cometa es la página más repetida, pero no la angular
    stats = aggregate(games, loaded_gamedata().item)[(AHRI, "MIDDLE")]
    # Electrocutar (5 de 9); en cada fila, lo más elegido entre quienes llevan Electrocutar
    assert stats.runes == (8100, 8200, 8112, 8139, 8140, 8106, 8226, 8210, 5005, 5008, 5001)
    assert stats.spells == (4, 14)  # Destello + Prender, en el orden de teclas más habitual


def test_stats_file_round_trip(tmp_path: Path) -> None:
    gd = loaded_gamedata()
    stats = aggregate(sample_builds(), gd.item)
    path = tmp_path / "builds-16.22.json"
    save_stats(path, "16.22", 6, stats)
    loaded = load_stats(path)
    assert loaded is not None and loaded[0] == "16.22" and loaded[1] == 6
    assert loaded[2] == stats
    path.write_text("{roto", encoding="utf-8")
    assert load_stats(path) is None


# --- Fuente de builds ---------------------------------------------------------------------


def stats_source(games: int) -> StatsBuildSource:
    entry = ChampionStats(AHRI, "MIDDLE", games, games // 2, (ItemCount(1056), ItemCount(2003, 2)),
                          3020, 0.95, (6655, 4645, 3089), (3135, 3157, 3102))  # fmt: skip
    source = StatsBuildSource(min_games=20)
    source.update("16.22", 500, {(AHRI, "MIDDLE"): entry})
    return source


def test_stats_build_is_used_when_there_is_enough_data() -> None:
    gd = loaded_gamedata()
    base = stats_source(40).base_build(gd.champion(AHRI), gd._profiles[AHRI], Position.MIDDLE, 11)
    assert base.core == (6655, 4645, 3089) and base.boots == 3020
    assert base.archetype == Archetype.MAGE_BURST  # el tipo sigue saliendo de las reglas
    assert "40 partidas" in base.source and "16.22" in base.source


def test_position_unknown_uses_the_most_played_one() -> None:
    gd = loaded_gamedata()
    base = stats_source(40).base_build(gd.champion(AHRI), None, Position.NONE, 11)
    assert "estadísticas" in base.source


def stats_entry(champion: int, position: str, games: int) -> ChampionStats:
    return ChampionStats(champion, position, games, games // 2, (), 3020, 0.9,
                         (6655, 4645, 3089), ())  # fmt: skip


def test_best_position_is_the_most_played_with_enough_data() -> None:
    source = StatsBuildSource(min_games=20)
    source.update("16.22", 500, {
        (AHRI, "MIDDLE"): stats_entry(AHRI, "MIDDLE", 40),
        (AHRI, "TOP"): stats_entry(AHRI, "TOP", 25),
        (ZED, "MIDDLE"): stats_entry(ZED, "MIDDLE", 10),
    })  # fmt: skip
    assert source.best_position(AHRI) == Position.MIDDLE
    assert source.best_position(ZED) == Position.NONE  # pocas partidas: no se fía
    assert source.best_position(1) == Position.NONE  # sin datos


async def test_without_assigned_position_the_build_uses_the_stats_position() -> None:
    """Herramienta de práctica / a ciegas: la posición más jugada en alto elo manda."""
    gd = loaded_gamedata()
    source = StatsBuildSource(min_games=20)
    source.update("16.22", 500, {(AHRI, "TOP"): stats_entry(AHRI, "TOP", 40)})
    recommender = BuildRecommender(gd, source, source.best_position)
    plan = await recommender.recommend_for(FixtureClient(), AHRI, Position.NONE, [], 11)
    assert plan is not None and plan.position == Position.TOP
    assert plan.source.startswith("estadísticas")
    assigned = await recommender.recommend_for(FixtureClient(), AHRI, Position.MIDDLE, [], 11)
    assert assigned is not None and assigned.position == Position.MIDDLE  # la asignada manda
    assert assigned.source == "reglas"  # sin datos de Ahri en medio


def test_stats_loadout_needs_enough_games_with_runes() -> None:
    entry = stats_entry(AHRI, "MIDDLE", 40)
    with_runes = dataclasses.replace(entry, loadout_games=25, runes=AHRI_RUNES, spells=(4, 14))
    source = StatsBuildSource(min_games=20)
    source.update("16.22", 500, {(AHRI, "MIDDLE"): with_runes})
    found = source.loadout_for(AHRI, Position.MIDDLE)
    assert found is not None and found.games == 25 and found.patch == "16.22"
    assert found.page.perk_ids == AHRI_RUNES[2:] and found.spells == (4, 14)
    few = dataclasses.replace(with_runes, loadout_games=5)  # partidas antiguas sin runas
    source.update("16.22", 500, {(AHRI, "MIDDLE"): few})
    assert source.loadout_for(AHRI, Position.MIDDLE) is None


def test_rules_are_used_without_enough_data_or_outside_the_rift() -> None:
    gd = loaded_gamedata()
    few = stats_source(5).base_build(gd.champion(AHRI), None, Position.MIDDLE, 11)
    assert few.source == "reglas"
    aram = stats_source(40).base_build(gd.champion(AHRI), None, Position.NONE, 12)
    assert aram.source == "reglas"


def test_boots_skipped_when_almost_nobody_buys_them() -> None:
    gd = loaded_gamedata()
    entry = ChampionStats(69, "MIDDLE", 30, 15, (), 3020, 0.05, (6653, 3116, 3089), ())
    source = StatsBuildSource()
    source.update("16.22", 300, {(69, "MIDDLE"): entry})
    assert source.base_build(gd.champion(69), None, Position.MIDDLE, 11).boots is None


# --- Preparación (parche y caché) ---------------------------------------------------------


def test_prepare_stats_uses_cache_and_falls_back_to_previous_patch(tmp_path: Path) -> None:
    gd = loaded_gamedata()
    store = MatchStore(tmp_path / "matches.sqlite3")
    for n in range(30):
        match, timeline = build_match(f"EUW1_A{n}", "16.21.1.1")
        store.add_match(f"EUW1_A{n}", "16.21", extract_builds(match, timeline))
    for n in range(5):
        match, timeline = build_match(f"EUW1_B{n}", "16.22.1.1")
        store.add_match(f"EUW1_B{n}", "16.22", extract_builds(match, timeline))
    store.close()

    result = prepare_stats(tmp_path, "16.22", gd)
    assert (
        result is not None and result[0] == "16.21" and result[1] == 30
    )  # parche nuevo, pocos datos
    cache = tmp_path / "builds-16.21.json"
    assert cache.exists()
    stamp = cache.stat().st_mtime_ns
    again = prepare_stats(tmp_path, "16.22", gd)
    assert again is not None and cache.stat().st_mtime_ns == stamp  # leído de la caché
    assert prepare_stats(tmp_path / "vacio", "16.22", gd) is None
