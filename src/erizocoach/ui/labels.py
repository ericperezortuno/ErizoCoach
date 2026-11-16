"""Textos en español para mostrar al usuario."""

from __future__ import annotations

from ..champselect.models import Position
from ..models import GameflowPhase

POSITION_LABELS = {
    Position.TOP: "Superior",
    Position.JUNGLE: "Jungla",
    Position.MIDDLE: "Medio",
    Position.BOTTOM: "Inferior",
    Position.UTILITY: "Soporte",
    Position.NONE: "",
}

GAMEFLOW_LABELS = {
    GameflowPhase.NONE: "En el menú principal",
    GameflowPhase.LOBBY: "En la sala",
    GameflowPhase.MATCHMAKING: "Buscando partida",
    GameflowPhase.CHECKED_INTO_TOURNAMENT: "Inscrito en torneo",
    GameflowPhase.READY_CHECK: "¡Partida encontrada!",
    GameflowPhase.CHAMP_SELECT: "Selección de campeones",
    GameflowPhase.GAME_START: "Iniciando partida",
    GameflowPhase.FAILED_TO_LAUNCH: "La partida no se pudo iniciar",
    GameflowPhase.IN_PROGRESS: "En partida",
    GameflowPhase.RECONNECT: "Pendiente de reconectar a la partida",
    GameflowPhase.WAITING_FOR_STATS: "Esperando estadísticas",
    GameflowPhase.PRE_END_OF_GAME: "Fin de partida",
    GameflowPhase.END_OF_GAME: "Fin de partida",
    # El cliente usa este estado también al salir con normalidad de la práctica: no lo mostramos.
    GameflowPhase.TERMINATED_IN_ERROR: "",
    GameflowPhase.UNKNOWN: "",
}
