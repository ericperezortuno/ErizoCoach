"""Modelo de la selección de campeones, independiente del formato JSON del cliente."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..models import Position

__all__ = ["ChampSelectPhase", "ChampSelectSnapshot", "PlayerSlot", "Position"]


class ChampSelectPhase(StrEnum):
    PLANNING = "PLANNING"  # declarar intención de campeón
    BAN_PICK = "BAN_PICK"  # baneos y picks
    FINALIZATION = "FINALIZATION"  # todos han elegido: aspectos, runas, intercambios
    GAME_STARTING = "GAME_STARTING"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: object) -> ChampSelectPhase:
        if isinstance(raw, str):
            try:
                return cls(raw.upper())
            except ValueError:
                pass
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class PlayerSlot:
    cell_id: int
    is_ally: bool
    is_local_player: bool
    champion_id: int  # 0 = sin campeón (o desconocido, en enemigos de selección a ciegas)
    locked: bool  # True = campeón bloqueado (definitivo salvo intercambio)
    position: Position  # en enemigos siempre NONE: el cliente no lo revela
    pick_intent_id: int = 0  # campeón "declarado" en la fase de planificación (aliados)
    spell1_id: int = 0
    spell2_id: int = 0

    @property
    def has_champion(self) -> bool:
        return self.champion_id > 0


@dataclass(frozen=True, slots=True)
class ChampSelectSnapshot:
    """Foto inmutable de la selección de campeones en un momento dado.

    Solo incluye lo que importa para decidir: el temporizador se omite a
    propósito para que dos fotos sin cambios reales sean iguales y no generen
    trabajo extra (el cliente manda una actualización cada segundo).
    """

    game_id: int
    phase: ChampSelectPhase
    local_cell_id: int
    my_team: tuple[PlayerSlot, ...]
    their_team: tuple[PlayerSlot, ...]
    ally_bans: tuple[int, ...] = ()
    enemy_bans: tuple[int, ...] = ()
    has_pick_actions: bool = True
    all_picks_completed: bool = False
    is_custom_game: bool = False
    bench_enabled: bool = False  # ARAM: banquillo de campeones

    @property
    def me(self) -> PlayerSlot | None:
        return next((slot for slot in self.my_team if slot.is_local_player), None)

    @property
    def allies(self) -> tuple[PlayerSlot, ...]:
        """Compañeros de equipo, sin incluirte a ti."""
        return tuple(slot for slot in self.my_team if not slot.is_local_player)

    @property
    def enemies(self) -> tuple[PlayerSlot, ...]:
        return self.their_team

    @property
    def slots(self) -> tuple[PlayerSlot, ...]:
        return self.my_team + self.their_team

    @property
    def known_enemies(self) -> tuple[PlayerSlot, ...]:
        return tuple(slot for slot in self.their_team if slot.locked and slot.has_champion)

    @property
    def enemies_known(self) -> bool:
        """False en selección a ciegas (no se ven los campeones enemigos)."""
        return bool(self.known_enemies)

    @property
    def is_finalized(self) -> bool:
        """Se ha hecho el último pick: momento de aplicar runas y hechizos."""
        if self.phase in (ChampSelectPhase.FINALIZATION, ChampSelectPhase.GAME_STARTING):
            return True
        return self.has_pick_actions and self.all_picks_completed
