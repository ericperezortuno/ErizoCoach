"""Fuente de builds por estadísticas, con las reglas como respaldo.

Para cada campeón y posición con suficientes partidas (20 por defecto) se usa la
build real de los jugadores de alto elo; si no hay datos suficientes (campeones
poco jugados, ARAM, parche recién salido...), la de las reglas. El tipo de
campeón sigue saliendo de las reglas: es lo que decide cómo adaptar la build a
los enemigos.
"""

from __future__ import annotations

from ..builds.archetypes import SUMMONERS_RIFT, BuildSource, RuleBasedSource
from ..builds.models import BaseBuild
from ..gamedata.catalog import Champion, ChampionProfile
from ..gamedata.runes import RunePage
from ..loadout.models import StatsLoadout
from ..models import Position
from .aggregate import ChampionStats
from .extract import RUNE_FIELDS

DEFAULT_MIN_GAMES = 20
MIN_BOOTS_RATE = 0.3  # si casi nadie compra botas (Cassiopeia...), no se recomiendan


class StatsBuildSource:
    def __init__(self, fallback: BuildSource | None = None, *, min_games: int = DEFAULT_MIN_GAMES):
        self._fallback = fallback or RuleBasedSource()
        self._min_games = min_games
        self._stats: dict[tuple[int, str], ChampionStats] = {}
        self.patch = ""
        self.matches = 0

    def update(self, patch: str, matches: int, stats: dict[tuple[int, str], ChampionStats]) -> None:
        self.patch, self.matches, self._stats = patch, matches, stats

    @property
    def covered(self) -> int:
        """Combinaciones campeón/posición con datos suficientes."""
        return sum(entry.games >= self._min_games for entry in self._stats.values())

    def best_position(self, champion_id: int) -> Position:
        """La posición en la que más se juega el campeón en alto elo (NONE si no hay datos)."""
        options = [
            e
            for (c, _), e in self._stats.items()
            if c == champion_id and e.games >= self._min_games
        ]
        best = max(options, key=lambda e: e.games, default=None)
        return Position.parse(best.position) if best else Position.NONE

    def entry_for(self, champion_id: int, position: Position) -> ChampionStats | None:
        if position != Position.NONE:
            entry = self._stats.get((champion_id, position.api_name))
        else:  # sin posición: la más jugada de ese campeón
            options = [e for (c, _), e in self._stats.items() if c == champion_id]
            entry = max(options, key=lambda e: e.games, default=None)
        return entry if entry is not None and entry.games >= self._min_games else None

    def loadout_for(self, champion_id: int, position: Position) -> StatsLoadout | None:
        """Runas y hechizos más jugados (solo si hay partidas suficientes con esos datos)."""
        entry = self.entry_for(champion_id, position)
        if (
            entry is None
            or entry.loadout_games < self._min_games
            or len(entry.runes) != RUNE_FIELDS
            or len(entry.spells) != 2
        ):
            return None
        page = RunePage(entry.runes[0], entry.runes[1], tuple(entry.runes[2:]))
        spells = (entry.spells[0], entry.spells[1])
        return StatsLoadout(page, spells, entry.loadout_games, self.patch)

    def base_build(
        self,
        champion: Champion | None,
        profile: ChampionProfile | None,
        position: Position,
        map_id: int,
    ) -> BaseBuild:
        base = self._fallback.base_build(champion, profile, position, map_id)
        if champion is None or map_id != SUMMONERS_RIFT:
            return base
        entry = self.entry_for(champion.id, position)
        if entry is None or len(entry.core) < 3:
            return base
        boots = entry.boots if entry.boots_rate >= MIN_BOOTS_RATE else None
        return BaseBuild(
            archetype=base.archetype,
            source=f"estadísticas de {entry.games} partidas de alto elo (parche {self.patch})",
            starting=entry.starting or base.starting,
            boots=boots,
            core=entry.core,
            extra=entry.extra or base.extra,
            support_upgrade=base.support_upgrade,
        )
