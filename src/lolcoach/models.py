"""Modelos generales del cliente: estado (gameflow), cola y datos de la instalación."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .util import as_dict, as_int, as_str


class Position(StrEnum):
    """Posición en la Grieta del Invocador."""

    TOP = "top"
    JUNGLE = "jungle"
    MIDDLE = "middle"
    BOTTOM = "bottom"
    UTILITY = "utility"
    NONE = ""

    @classmethod
    def parse(cls, raw: object) -> Position:
        """Acepta el formato de la selección (``"middle"``) y el de las runas (``"MIDDLE"``)."""
        if isinstance(raw, str):
            try:
                return cls(raw.lower())
            except ValueError:
                pass
        return cls.NONE

    @property
    def api_name(self) -> str:
        """Formato de las rutas de runas recomendadas: ``MIDDLE``, ``NONE``..."""
        return self.value.upper() or "NONE"


LANE_POSITIONS = (
    Position.TOP,
    Position.JUNGLE,
    Position.MIDDLE,
    Position.BOTTOM,
    Position.UTILITY,
)


class GameflowPhase(StrEnum):
    """Estado general del cliente (sala, cola, selección, partida...)."""

    NONE = "None"
    LOBBY = "Lobby"
    MATCHMAKING = "Matchmaking"
    CHECKED_INTO_TOURNAMENT = "CheckedIntoTournament"
    READY_CHECK = "ReadyCheck"
    CHAMP_SELECT = "ChampSelect"
    GAME_START = "GameStart"
    FAILED_TO_LAUNCH = "FailedToLaunch"
    IN_PROGRESS = "InProgress"
    RECONNECT = "Reconnect"
    WAITING_FOR_STATS = "WaitingForStats"
    PRE_END_OF_GAME = "PreEndOfGame"
    END_OF_GAME = "EndOfGame"
    TERMINATED_IN_ERROR = "TerminatedInError"
    UNKNOWN = "Unknown"

    @classmethod
    def parse(cls, raw: object) -> GameflowPhase:
        if isinstance(raw, str):
            try:
                return cls(raw)
            except ValueError:
                pass
        return cls.UNKNOWN

    @property
    def is_game_starting(self) -> bool:
        """La selección de campeones terminó con éxito y la partida arranca."""
        return self in (
            GameflowPhase.GAME_START,
            GameflowPhase.IN_PROGRESS,
            GameflowPhase.RECONNECT,
        )


@dataclass(frozen=True, slots=True)
class ClientInfo:
    version: str
    region: str
    locale: str

    @property
    def patch(self) -> str:
        """``"16.22.834.8910"`` -> ``"16.22"``."""
        parts = self.version.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else self.version


@dataclass(frozen=True, slots=True)
class QueueInfo:
    id: int
    name: str
    game_mode: str  # CLASSIC, ARAM, CHERRY...
    map_id: int

    @classmethod
    def from_gameflow_session(cls, data: Any) -> QueueInfo | None:
        session = as_dict(data)
        game_data = as_dict(session.get("gameData"))
        queue = as_dict(game_data.get("queue"))
        if not queue:
            return None
        return cls(
            id=as_int(queue.get("id"), -1),
            name=as_str(queue.get("description")) or as_str(queue.get("name")),
            game_mode=as_str(queue.get("gameMode")),
            map_id=as_int(queue.get("mapId")) or as_int(as_dict(session.get("map")).get("id")),
        )
