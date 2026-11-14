"""Bus de eventos, mensajes del websocket, registros, configuración e instancia única."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lolcoach.bus import EventBus
from lolcoach.config import load_settings
from lolcoach.lcu.websocket import parse_message, topic_for
from lolcoach.logs import RedactingFilter
from lolcoach.models import ClientInfo, GameflowPhase, QueueInfo
from lolcoach.single_instance import SingleInstanceLock

# --- Bus -------------------------------------------------------------------


async def test_bus_delivers_to_sync_and_async_handlers_in_order() -> None:
    bus = EventBus()
    received: list[str] = []

    async def async_handler(event: str) -> None:
        received.append(f"async:{event}")

    bus.subscribe(str, lambda e: received.append(f"sync:{e}"))
    bus.subscribe(str, async_handler)
    await bus.publish("hola")
    assert received == ["sync:hola", "async:hola"]


async def test_bus_isolates_failing_handler(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    received: list[int] = []

    def broken(_: int) -> None:
        raise RuntimeError("fallo")

    bus.subscribe(int, broken)
    bus.subscribe(int, received.append)
    with caplog.at_level(logging.ERROR):
        await bus.publish(1)
    assert received == [1]
    assert "Error al procesar el evento int" in caplog.text


async def test_bus_unsubscribe() -> None:
    bus = EventBus()
    received: list[int] = []
    unsubscribe = bus.subscribe(int, received.append)
    unsubscribe()
    unsubscribe()  # idempotente
    await bus.publish(1)
    assert received == []


# --- Websocket ---------------------------------------------------------------


def test_topic_for() -> None:
    assert topic_for("/lol-champ-select/v1/session") == "OnJsonApiEvent_lol-champ-select_v1_session"


def test_parse_message() -> None:
    raw = json.dumps(
        [
            8,
            "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase",
            {
                "data": "ChampSelect",
                "eventType": "Update",
                "uri": "/lol-gameflow/v1/gameflow-phase",
            },
        ]
    )
    event = parse_message(raw)
    assert event is not None
    assert event.uri == "/lol-gameflow/v1/gameflow-phase"
    assert event.data == "ChampSelect" and not event.deleted


@pytest.mark.parametrize(
    "raw", ["", "no json", "[]", '[5, "x"]', '[8, "t", "x"]', '[8, "t", {"uri": 1}]']
)
def test_parse_message_ignores_garbage(raw: str) -> None:
    assert parse_message(raw) is None


# --- Registros -----------------------------------------------------------------


def test_redacting_filter_hides_secrets() -> None:
    redactor = RedactingFilter()
    redactor.add_secret("pwd123", "cmlvdDpwd2QxMjM=", "")
    record = logging.LogRecord(
        "x",
        logging.INFO,
        __file__,
        1,
        "token %s y cabecera Basic %s",
        ("pwd123", "cmlvdDpwd2QxMjM="),
        None,
    )
    redactor.filter(record)
    assert record.getMessage() == "token *** y cabecera Basic ***"


def test_redacting_filter_also_hides_secrets_in_tracebacks() -> None:
    """El texto del error (Traceback) se escribe aparte del mensaje: también se oculta."""
    redactor = RedactingFilter()
    redactor.add_secret("RGAPI-secreta")
    try:
        raise ValueError("fallo con RGAPI-secreta dentro")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "error", (), sys.exc_info())
    redactor.filter(record)
    text = logging.Formatter().format(record)
    assert "RGAPI-secreta" not in text and "***" in text and "ValueError" in text


# --- Modelos ---------------------------------------------------------------------


def test_gameflow_phase_parse() -> None:
    assert GameflowPhase.parse("ChampSelect") == GameflowPhase.CHAMP_SELECT
    assert GameflowPhase.parse("SomethingNew") == GameflowPhase.UNKNOWN
    assert GameflowPhase.parse(None) == GameflowPhase.UNKNOWN
    assert GameflowPhase.GAME_START.is_game_starting
    assert not GameflowPhase.LOBBY.is_game_starting


def test_client_info_patch() -> None:
    assert ClientInfo("16.22.834.8910", "EUW", "es_ES").patch == "16.22"


def test_queue_info_from_gameflow_session() -> None:
    data = {
        "gameData": {
            "isCustomGame": False,
            "queue": {
                "id": 440,
                "description": "Clasificatoria flexible",
                "gameMode": "CLASSIC",
                "type": "RANKED_FLEX_SR",
                "pickMode": "TeamBuilderDraftPickStrategy",
                "isRanked": True,
                "mapId": 11,
            },
        },
        "map": {"id": 11},
    }
    queue = QueueInfo.from_gameflow_session(data)
    assert queue is not None
    assert (queue.id, queue.name, queue.game_mode, queue.map_id) == (
        440,
        "Clasificatoria flexible",
        "CLASSIC",
        11,
    )
    assert QueueInfo.from_gameflow_session({}) is None


# --- Configuración ---------------------------------------------------------------


def test_settings_defaults(tmp_path: Path) -> None:
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.league_dir is None and not settings.debug
    assert settings.discovery_interval == 3.0 and warnings == []


def test_settings_from_file_and_cli_override(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'league_dir = "D:/Juegos/LoL"\ndebug = true\ndiscovery_interval = 5\n', encoding="utf-8"
    )
    settings, warnings = load_settings(data_dir=tmp_path, debug=False)
    assert settings.league_dir == Path("D:/Juegos/LoL")
    assert settings.debug is False  # la línea de comandos manda
    assert settings.discovery_interval == 5.0 and warnings == []


def test_settings_invalid_values_warn(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('debug = "si"\ndiscovery_interval = 0\n')
    settings, warnings = load_settings(data_dir=tmp_path)
    assert not settings.debug and settings.discovery_interval == 3.0
    assert len(warnings) == 2


def test_settings_broken_file_warns(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("esto no es toml = = =")
    _, warnings = load_settings(data_dir=tmp_path)
    assert len(warnings) == 1


# --- Instancia única ---------------------------------------------------------------


def test_single_instance_lock(tmp_path: Path) -> None:
    path = tmp_path / "app.lock"
    first, second = SingleInstanceLock(path), SingleInstanceLock(path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
