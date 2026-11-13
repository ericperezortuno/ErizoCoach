"""Pruebas de punta a punta contra el cliente simulado (TLS real, websocket real)."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from pathlib import Path

import pytest
import trustme

from lolcoach.events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    LoadoutApplied,
    MyChampionChanged,
    PicksFinalized,
)
from lolcoach.lcu import endpoints
from lolcoach.lcu.tls import create_ssl_context
from lolcoach.models import GameflowPhase, Position

from .factories import AHRI, DraftScenario
from .fake_lcu import FakeLcu
from .harness import Harness, credentials_for, wait_until


async def test_full_ranked_draft_flow(
    fake: FakeLcu, client_ssl: ssl.SSLContext, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="lolcoach")
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
        assert len(fake.subscriptions) == 2
        assert app.gamedata.champion_name(AHRI) == "Ahri"

        await fake.set_phase("ChampSelect")
        await wait_until(lambda: endpoints.CHAMP_SELECT_SESSION in fake.requests)
        scenario = DraftScenario()
        for picks in range(11):
            await fake.set_session(scenario.state(picks))
        await wait_until(lambda: bool(app.of(PicksFinalized)))

        await fake.set_phase("GameStart")
        await wait_until(lambda: bool(app.of(ChampSelectEnded)))

        await fake.drop_connections()  # el cliente se cierra...
        await wait_until(lambda: bool(app.of(ClientDisconnected)))
        await wait_until(lambda: len(app.of(ClientConnected)) == 2)  # ...y se reconecta solo

    types = [type(e) for e in app.events]
    assert types[:4] == [
        ClientConnected,
        GameflowPhaseChanged,
        GameflowPhaseChanged,
        ChampSelectStarted,
    ]
    assert types.count(ChampionLocked) == 10
    assert types.count(MyChampionChanged) == 1
    assert types.count(PicksFinalized) == 1

    started = app.of(ChampSelectStarted)[0]
    assert started.queue is not None and started.queue.name == "Clasificatoria flexible"
    finalized = app.of(PicksFinalized)[0]
    assert finalized.snapshot.me.champion_id == AHRI
    assert finalized.queue is not None and finalized.queue.map_id == 11
    assert app.of(ChampSelectEnded)[0].outcome == ChampSelectOutcome.GAME_STARTING

    # Seguridad: ni la contraseña ni su versión codificada aparecen en ningún mensaje.
    for secret in credentials_for(fake).secrets:
        assert secret not in caplog.text


async def test_app_started_in_the_middle_of_champ_select(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    fake.phase = "ChampSelect"
    fake.session = DraftScenario().state(10)
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: bool(app.of(PicksFinalized)))
    assert app.of(ChampSelectStarted) and app.of(MyChampionChanged)[0].champion_id == AHRI


async def test_dodge_is_reported_as_cancelled(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    fake.phase = "ChampSelect"
    fake.session = DraftScenario().state(3)
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: bool(app.of(ChampSelectStarted)))
        await fake.delete_session()
        await fake.set_phase("Matchmaking")
        await wait_until(lambda: bool(app.of(ChampSelectEnded)))
    assert app.of(ChampSelectEnded)[0].outcome == ChampSelectOutcome.CANCELLED
    assert not app.of(PicksFinalized)


async def test_waits_while_client_is_starting_up(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    fake.ready = False
    async with Harness(credentials_for(fake), client_ssl) as app:
        await asyncio.sleep(0.3)
        assert not app.of(ClientConnected)
        fake.ready = True
        await wait_until(lambda: bool(app.of(ClientConnected)))


async def test_rejects_server_with_untrusted_certificate(
    fake: FakeLcu, caplog: pytest.LogCaptureFixture
) -> None:
    impostor_trust = create_ssl_context(trustme.CA().cert_pem.bytes().decode("ascii"))
    with caplog.at_level(logging.ERROR, logger="lolcoach"):
        async with Harness(credentials_for(fake), impostor_trust) as app:
            await asyncio.sleep(0.5)
    assert not app.of(ClientConnected)
    assert caplog.text.count("Conexión rechazada por seguridad") == 1  # se avisa una sola vez


REPLAY = Path(__file__).parent / "fixtures" / "practice_tool_replay.json"
LEBLANC, AKALI = 7, 84


async def test_replay_of_real_practice_tool_session(
    fake: FakeLcu, client_ssl: ssl.SSLContext
) -> None:
    """Eventos grabados del cliente real (anonimizados), reproducidos en su orden original.

    Casos reales que cubre: la sesión llega antes de que el estado pase a
    "ChampSelect", el jugador mira Akali y LeBlanc antes de bloquear LeBlanc, y la
    Herramienta de práctica tiene huecos enemigos vacíos.
    """
    recorded = json.loads(REPLAY.read_text(encoding="utf-8"))
    async with Harness(credentials_for(fake), client_ssl) as app:
        await wait_until(lambda: app.monitor.phase == GameflowPhase.LOBBY)
        for event in recorded:
            if event["uri"] == endpoints.GAMEFLOW_PHASE:
                await fake.set_phase(event["data"])
            elif event["eventType"] == "Delete":
                await fake.delete_session()
            else:
                await fake.set_session(event["data"])
            await asyncio.sleep(0.02)
        await wait_until(lambda: app.monitor.phase == GameflowPhase.IN_PROGRESS)
        await wait_until(lambda: bool(app.of(ChampSelectEnded)))

    phases = [e.current for e in app.of(GameflowPhaseChanged)]
    assert phases == [
        GameflowPhase.LOBBY, GameflowPhase.MATCHMAKING, GameflowPhase.READY_CHECK,
        GameflowPhase.CHAMP_SELECT, GameflowPhase.GAME_START, GameflowPhase.IN_PROGRESS,
    ]  # fmt: skip
    assert len(app.of(ChampSelectStarted)) == 1
    assert [e.slot.champion_id for e in app.of(ChampionLocked)] == [LEBLANC]
    assert [e.champion_id for e in app.of(MyChampionChanged)] == [LEBLANC]  # Akali solo se miró
    finalized = app.of(PicksFinalized)
    assert len(finalized) == 1 and finalized[0].snapshot.me.champion_id == LEBLANC
    assert not finalized[0].snapshot.enemies_known
    assert finalized[0].snapshot.me.spell1_id == 4  # Destello en la D
    assert app.of(ChampSelectEnded)[0].outcome == ChampSelectOutcome.GAME_STARTING

    # Fase 2, como en la prueba real: runas de LeBlanc (medio) y Destello + Prender
    applied = app.of(LoadoutApplied)
    assert len(applied) == 1 and applied[0].runes_applied and applied[0].spells_applied
    loadout = applied[0].loadout
    assert loadout.champion_id == LEBLANC and loadout.position == Position.MIDDLE
    assert loadout.runes is not None and loadout.runes.keystone_id == 8112  # Electrocutar
    assert loadout.spells == (4, 14)
    assert fake.writes_to(endpoints.CHAMP_SELECT_MY_SELECTION) == []  # ya los tenía


async def test_wrong_password_never_connects(fake: FakeLcu, client_ssl: ssl.SSLContext) -> None:
    async with Harness(credentials_for(fake, password="incorrecta"), client_ssl) as app:
        await asyncio.sleep(0.5)
    assert not app.of(ClientConnected)
