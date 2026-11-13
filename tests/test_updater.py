"""Descarga automática de estadísticas: tandas, pausa al jugar, clave caducada y bloqueo."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from lolcoach.bus import EventBus
from lolcoach.config import Settings
from lolcoach.events import GameflowPhaseChanged, StatsStatus
from lolcoach.models import ClientInfo, GameflowPhase
from lolcoach.stats.crawler import Crawler, CrawlProgress
from lolcoach.stats.riot_api import RiotApiClient
from lolcoach.stats.store import MatchStore
from lolcoach.stats.updater import StatsUpdater, download_lock

from .fake_riot_api import API_KEY, FakeRiotApi
from .stats_helpers import corrupt_database, corruption_error, fill_store

INFO = ClientInfo("16.22.834.8910", "EUW1", "es_ES")


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def riot() -> AsyncIterator[FakeRiotApi]:
    api = FakeRiotApi()
    await api.start()
    yield api
    await api.stop()


class Refresher:
    def __init__(self) -> None:
        self.patches: list[str] = []

    async def refresh(self, patch: str) -> None:
        self.patches.append(patch)


def make_updater(
    riot: FakeRiotApi, tmp_path: Path, *, key: str | None = API_KEY, **settings: object
) -> tuple[StatsUpdater, EventBus, Refresher, list[StatsStatus]]:
    bus = EventBus()
    statuses: list[StatsStatus] = []
    bus.subscribe(StatsStatus, statuses.append)
    refresher = Refresher()
    values: dict[str, object] = {"stats_target_matches": 5} | settings
    updater = StatsUpdater(
        bus,
        None,
        refresher,
        Settings(data_dir=tmp_path, **values),  # type: ignore[arg-type]
        key_loader=lambda: key,
        api_factory=lambda k, _region: RiotApiClient(k, hosts=(riot.url, riot.url), sleep=no_sleep),
        retry_seconds=0.01,
    )
    return updater, bus, refresher, statuses


async def finish(updater: StatsUpdater) -> None:
    assert updater.task is not None
    await asyncio.wait_for(updater.task, 10)


async def test_downloads_until_the_target_and_refreshes_the_stats(
    riot: FakeRiotApi, tmp_path: Path
) -> None:
    updater, _bus, refresher, statuses = make_updater(riot, tmp_path)
    await updater._on_connect(None, INFO)
    await finish(updater)
    store = MatchStore(tmp_path / "stats" / "matches.sqlite3")
    assert store.match_count("16.22") >= 5 and store.loadout_count("16.22") >= 5
    store.close()
    assert refresher.patches == ["16.22"]  # la app ya usa los datos nuevos
    assert "al día" in statuses[-1].text and not statuses[-1].problem

    requests = len(riot.requests)
    await updater._on_connect(None, INFO)  # mismo parche y completo: no vuelve a descargar
    assert not updater.running and len(riot.requests) == requests


async def test_one_client_for_all_batches(riot: FakeRiotApi, tmp_path: Path) -> None:
    """Las tandas comparten cliente: los límites de Riot se respetan también entre tandas."""
    updater, _bus, refresher, _statuses = make_updater(riot, tmp_path, stats_target_matches=11)
    created: list[RiotApiClient] = []
    original = updater._api_factory

    def counting_factory(key: str, region: str) -> RiotApiClient:
        created.append(original(key, region))
        return created[-1]

    updater._api_factory = counting_factory
    updater._batch_seconds = 0.01  # tandas muy cortas: hacen falta varias
    await updater._on_connect(None, INFO)
    await finish(updater)
    assert len(refresher.patches) >= 2 and len(created) == 1


async def test_pauses_while_playing_and_resumes_in_the_menu(
    riot: FakeRiotApi, tmp_path: Path
) -> None:
    updater, bus, _refresher, _statuses = make_updater(riot, tmp_path, stats_target_matches=200)
    await updater._on_connect(None, INFO)
    assert updater.running
    await bus.publish(GameflowPhaseChanged(GameflowPhase.LOBBY, GameflowPhase.CHAMP_SELECT))
    assert not updater.running
    requests = len(riot.requests)
    await updater._on_connect(None, INFO)  # p. ej., reconexión durante la selección
    await asyncio.sleep(0.05)
    assert not updater.running and len(riot.requests) == requests  # nada mientras juegas
    await bus.publish(GameflowPhaseChanged(GameflowPhase.IN_PROGRESS, GameflowPhase.END_OF_GAME))
    assert updater.running
    await updater.stop()


async def test_expired_key_stops_and_warns(riot: FakeRiotApi, tmp_path: Path) -> None:
    riot.expired = True
    updater, _bus, refresher, statuses = make_updater(riot, tmp_path)
    await updater._on_connect(None, INFO)
    await finish(updater)
    assert statuses[-1].problem and "stats clave" in statuses[-1].text
    assert API_KEY not in statuses[-1].text
    assert refresher.patches == []


async def test_without_key_nothing_is_downloaded(riot: FakeRiotApi, tmp_path: Path) -> None:
    updater, _bus, _refresher, statuses = make_updater(riot, tmp_path, key=None)
    await updater._on_connect(None, INFO)
    await finish(updater)
    assert riot.requests == [] and "no hay clave" in statuses[-1].text


async def test_disabled_in_settings(riot: FakeRiotApi, tmp_path: Path) -> None:
    updater, _bus, _refresher, _statuses = make_updater(riot, tmp_path, stats_auto_download=False)
    await updater._on_connect(None, INFO)
    assert updater.task is None and riot.requests == []


async def test_corrupt_database_is_replaced_and_the_window_warns(
    riot: FakeRiotApi, tmp_path: Path
) -> None:
    database = tmp_path / "stats" / "matches.sqlite3"
    fill_store(database)
    corrupt_database(database)
    updater, _bus, refresher, statuses = make_updater(riot, tmp_path)
    await updater._on_connect(None, INFO)
    await finish(updater)  # sin excepciones: la descarga sigue con una base nueva
    moved = list(database.parent.glob("matches.corrupt-*.sqlite3"))
    assert len(moved) == 1
    warnings = [s for s in statuses if s.problem]
    assert warnings and "dañada" in warnings[0].text and moved[0].name in warnings[0].text
    assert "al día" in statuses[-1].text and refresher.patches == ["16.22"]
    store = MatchStore(database)
    assert store.match_count("16.22") >= 5
    store.close()


async def test_damage_found_during_a_batch_does_not_stop_the_updater(
    riot: FakeRiotApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Como a las 10:06 del incidente: la base se daña con la tanda en marcha."""
    error = corruption_error(tmp_path / "otra")
    original = Crawler.run
    calls: list[int] = []

    async def damaged_once(self: Crawler) -> CrawlProgress:
        calls.append(1)
        if len(calls) == 1:
            raise error
        return await original(self)

    monkeypatch.setattr(Crawler, "run", damaged_once)
    updater, _bus, _refresher, statuses = make_updater(riot, tmp_path)
    await updater._on_connect(None, INFO)
    await finish(updater)
    assert len(list((tmp_path / "stats").glob("matches.corrupt-*.sqlite3"))) == 1
    assert any(s.problem and "dañada" in s.text for s in statuses)
    assert "al día" in statuses[-1].text


async def test_a_busy_database_is_retried_without_setting_it_aside(
    riot: FakeRiotApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Crawler.run
    calls: list[int] = []

    async def locked_once(self: Crawler) -> CrawlProgress:
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return await original(self)

    monkeypatch.setattr(Crawler, "run", locked_once)
    updater, _bus, _refresher, statuses = make_updater(riot, tmp_path)
    await updater._on_connect(None, INFO)
    await finish(updater)
    assert not list((tmp_path / "stats").glob("matches.corrupt-*"))
    assert "al día" in statuses[-1].text and not any(s.problem for s in statuses)


async def test_waits_while_another_download_runs(riot: FakeRiotApi, tmp_path: Path) -> None:
    updater, _bus, _refresher, statuses = make_updater(riot, tmp_path)
    lock = download_lock(tmp_path / "stats")
    assert lock.acquire()  # «lolcoach stats descargar» en la consola
    try:
        await updater._on_connect(None, INFO)
        await asyncio.sleep(0.1)
        assert updater.running and riot.requests == []
        assert "otra descarga" in statuses[-1].text
    finally:
        lock.release()
    await finish(updater)  # al terminar la otra, sigue sola
    assert riot.requests
