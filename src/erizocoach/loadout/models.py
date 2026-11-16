"""Modelos de la recomendación de runas y hechizos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..gamedata.catalog import ChampionProfile
from ..gamedata.runes import RecommendedPage, RunePage
from ..models import Position
from .positions import LaneGuess

# Umbrales calibrados con los 173 campeones jugables (parche 16.22):
# el control medio de un equipo enemigo suma ~10 puntos (0-3 por campeón).
LANE_CONFIDENCE = 0.6  # a partir de aquí damos por bueno el rival de línea
HEAVY_CC_TOTAL = 12  # ~1 de cada 10 equipos
HARD_CC_CHAMPIONS = 3  # campeones con control máximo (3/3)
DIVE_THREATS = 2  # asesinos móviles con daño máximo


@dataclass(frozen=True, slots=True)
class MatchContext:
    champion_id: int
    position: Position
    map_id: int
    game_mode: str
    me: ChampionProfile | None
    enemies: tuple[ChampionProfile, ...] = ()
    lane_guess: LaneGuess | None = None
    lane_opponent: ChampionProfile | None = None
    current_spells: tuple[int, int] = (0, 0)

    @property
    def enemies_known(self) -> bool:
        return bool(self.enemies)

    @property
    def on_summoners_rift(self) -> bool:
        return self.map_id == 11

    @property
    def confident_lane_opponent(self) -> ChampionProfile | None:
        if self.lane_guess is None or self.lane_guess.probability < LANE_CONFIDENCE:
            return None
        return self.lane_opponent

    @property
    def enemy_cc_total(self) -> int:
        return sum(e.crowd_control for e in self.enemies)

    @property
    def hard_cc_count(self) -> int:
        return sum(e.crowd_control >= 3 for e in self.enemies)

    @property
    def heavy_cc(self) -> bool:
        return self.hard_cc_count >= HARD_CC_CHAMPIONS or self.enemy_cc_total >= HEAVY_CC_TOTAL

    @property
    def dive_threats(self) -> int:
        return sum(e.is_dive_threat for e in self.enemies)


@dataclass(frozen=True, slots=True)
class RuneOption:
    """Una de las páginas que se ofrecen para elegir (piedras angulares distintas)."""

    page: RunePage
    detail: str  # p. ej. "La más jugada · 412 partidas · 53 % de victorias"
    recommended: bool = False  # la que Erizo Coach pone por defecto


@dataclass(frozen=True, slots=True)
class Loadout:
    """Runas y hechizos recomendados, con la explicación de cada decisión."""

    champion_id: int
    position: Position
    base: RecommendedPage | None  # recomendación de Riot usada como punto de partida
    runes: RunePage | None  # None = este modo no usa runas recomendadas
    spells: tuple[int, int] | None
    lane_guess: LaneGuess | None = None
    adjustments: tuple[str, ...] = ()  # cambios sobre la base y su motivo
    notes: tuple[str, ...] = ()  # avisos (enemigos desconocidos, rival dudoso...)
    source: str = ""  # de dónde sale la base: estadísticas o recomendación de Riot
    options: tuple[RuneOption, ...] = ()  # hasta 3 páginas para elegir (la 1.ª, la recomendada)


@dataclass(frozen=True, slots=True)
class StatsRuneOption:
    page: RunePage
    games: int
    wins: int


@dataclass(frozen=True, slots=True)
class StatsLoadout:
    """Runas y hechizos más jugados en alto elo para un campeón y posición."""

    page: RunePage
    spells: tuple[int, int]
    games: int
    patch: str


class LoadoutStats(Protocol):
    def loadout_for(self, champion_id: int, position: Position) -> StatsLoadout | None: ...

    def rune_options_for(
        self, champion_id: int, position: Position
    ) -> tuple[StatsRuneOption, ...]: ...


@dataclass(frozen=True, slots=True)
class CounterPick:
    """Un campeón contra tu rival de línea, según las partidas de alto elo."""

    champion_id: int
    games: int
    wins: int
    win_rate: float  # 0-1, mezclado con lo que gana ese campeón en general (ver counters.py)
