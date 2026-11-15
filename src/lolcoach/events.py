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
from .gamedata.runes import RunePage
from .ingame.advisor import Advice
from .ingame.analysis import GameReport
from .ingame.models import LiveGame
from .loadout.models import CounterPick, Loadout
from .models import ClientInfo, GameflowPhase, Position, QueueInfo


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
class MyPickPreviewChanged:
    """Antes del último pick: cambia el campeón que preseleccionas, miras o bloqueas."""

    champion_id: int  # 0 = ya no miras ninguno
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
class LoadoutPreview:
    """Runas y hechizos que tendría el campeón preseleccionado (aún no se aplican)."""

    champion_id: int  # 0 = sin campeón preseleccionado
    loadout: Loadout | None


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
class LiveGameUpdated:
    """Estado de la partida en curso (cada pocos segundos, solo si algo cambia)."""

    game: LiveGame
    report: GameReport
    advice: Advice | None  # None si no hay build para tu campeón (modo sin build)
    plan: BuildPlan | None


@dataclass(frozen=True, slots=True)
class LiveGameEnded:
    pass


@dataclass(frozen=True, slots=True)
class StatsStatus:
    """Estado de la descarga automática de estadísticas (para la consola y la ventana)."""

    text: str
    problem: bool = False  # algo que tienes que resolver tú (p. ej., la clave ha caducado)


@dataclass(frozen=True, slots=True)
class LoadoutApplied:
    loadout: Loadout
    page_name: str | None  # página de runas usada (None si no se aplicaron runas)
    runes_applied: bool
    spells_applied: bool
    problems: tuple[str, ...] = ()
    provisional: bool = False  # puestas al preseleccionar: al último pick se ajustan a los rivales


class RunesBy(StrEnum):
    """Quién ha puesto las runas (o los hechizos) que tienes ahora en el cliente."""

    NOBODY = ""
    PREVIEW = "preview"  # Erizo Coach, al preseleccionar (provisionales)
    COACH = "coach"  # Erizo Coach, al hacerse el último pick
    YOU_IN_APP = "you_in_app"  # tú, desde la ventana de Erizo Coach
    YOU_IN_CLIENT = "you_in_client"  # tú, en el cliente de LoL (se respetan)

    @property
    def is_yours(self) -> bool:
        return self in (RunesBy.YOU_IN_APP, RunesBy.YOU_IN_CLIENT)


@dataclass(frozen=True, slots=True)
class LoadoutInClient:
    """Lo que hay ahora en el cliente (lo que se ha escrito o lo que has cambiado tú)."""

    champion_id: int
    runes: RunePage | None
    spells: tuple[int, int] | None
    runes_by: RunesBy = RunesBy.NOBODY
    spells_by: RunesBy = RunesBy.NOBODY
    page_name: str | None = None
    problem: str = ""


@dataclass(frozen=True, slots=True)
class RunesEditRequested:
    """Desde la ventana: pon esta página de runas en el cliente."""

    page: RunePage


@dataclass(frozen=True, slots=True)
class SpellsEditRequested:
    """Desde la ventana: pon estos hechizos (D, F) en el cliente."""

    spells: tuple[int, int]


@dataclass(frozen=True, slots=True)
class LaunchClientRequested:
    """Desde la ventana: abre el cliente de League of Legends (botón «Abrir»)."""


class LaunchState(StrEnum):
    IDLE = "idle"  # nada en marcha (o se dejó de esperar): el botón se puede pulsar
    STARTING = "starting"  # el cliente de Riot está abriendo League of Legends
    NOT_FOUND = "notFound"  # no se encuentra el cliente de Riot en este PC
    FAILED = "failed"  # Windows no ha dejado abrirlo


@dataclass(frozen=True, slots=True)
class ClientLaunchStatus:
    state: LaunchState


@dataclass(frozen=True, slots=True)
class CountersUpdated:
    """Counters contra tu rival de línea en la selección (``enemy_id`` 0 = no hay que mostrar)."""

    position: Position
    enemy_id: int
    picks: tuple[CounterPick, ...]
    mine: CounterPick | None = None  # ya has bloqueado: cómo va tu enfrentamiento
    locked: bool = False
