"""Eventos de la aplicación.

Es el "contrato" entre módulos. Las fases siguientes se enganchan aquí, por ejemplo:

* Fase 2 (runas y hechizos): ``PicksFinalized`` y ``MyChampionChanged``.
* Fase 3 (build inicial): ``PicksFinalized`` y ``MyChampionChanged``.
* Fase 4 (durante la partida): ``ChampSelectEnded`` con ``GAME_STARTING`` y ``BuildApplied``
  (la build de partida que se irá adaptando).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .builds.models import BuildPlan
from .champselect.models import ChampSelectSnapshot, PlayerSlot
from .loadout.models import Loadout
from .models import ClientInfo, GameflowPhase, QueueInfo


@dataclass(frozen=True, slots=True)
class ClientConnected:
    info: ClientInfo


@dataclass(frozen=True, slots=True)
class ClientDisconnected:
    pass


@dataclass(frozen=True, slots=True)
class GameflowPhaseChanged:
    previous: GameflowPhase
    current: GameflowPhase


@dataclass(frozen=True, slots=True)
class ChampSelectStarted:
    snapshot: ChampSelectSnapshot
    queue: QueueInfo | None


@dataclass(frozen=True, slots=True)
class ChampionLocked:
    """Un jugador (tú, un aliado o un enemigo visible) ha bloqueado su campeón."""

    slot: PlayerSlot
    snapshot: ChampSelectSnapshot


@dataclass(frozen=True, slots=True)
class MyChampionChanged:
    """Tu campeón definitivo es nuevo: lo has bloqueado o lo has intercambiado."""

    champion_id: int
    previous_champion_id: int  # 0 si es el primer bloqueo
    snapshot: ChampSelectSnapshot


@dataclass(frozen=True, slots=True)
class PicksFinalized:
    """Se ha hecho el último pick. Se emite una sola vez por selección de campeones."""

    snapshot: ChampSelectSnapshot
    queue: QueueInfo | None


class ChampSelectOutcome(StrEnum):
    GAME_STARTING = "game_starting"
    CANCELLED = "cancelled"  # alguien la ha abandonado (dodge) o se ha salido


@dataclass(frozen=True, slots=True)
class ChampSelectEnded:
    outcome: ChampSelectOutcome
    snapshot: ChampSelectSnapshot
    queue: QueueInfo | None


@dataclass(frozen=True, slots=True)
class LoadoutReady:
    """Runas y hechizos calculados para tu campeón (antes de aplicarlos)."""

    loadout: Loadout


@dataclass(frozen=True, slots=True)
class BuildReady:
    """Build calculada para tu campeón (antes de llevarla a la tienda)."""

    plan: BuildPlan


@dataclass(frozen=True, slots=True)
class BuildApplied:
    plan: BuildPlan
    set_title: str | None  # conjunto de objetos de la tienda (None si no se escribió)
    applied: bool
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadoutApplied:
    loadout: Loadout
    page_name: str | None  # página de runas usada (None si no se aplicaron runas)
    runes_applied: bool
    spells_applied: bool
    problems: tuple[str, ...] = ()
