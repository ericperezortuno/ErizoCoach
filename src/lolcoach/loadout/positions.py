"""Deducción por probabilidad de la línea de cada enemigo.

El cliente no dice en qué línea va cada enemigo. Lo estimamos así:

1. Cada campeón tiene posiciones habituales, de más a menos frecuente (datos de Riot).
2. Probamos todos los repartos posibles de enemigos en líneas (como máximo
   5! = 120): cada línea la ocupa un solo campeón.
3. Cada reparto pesa según lo habitual que es cada campeón en su línea. Al
   normalizar obtenemos la probabilidad de que el enemigo X vaya a la línea Y.

Así, un campeón que puede ir a dos líneas queda colocado en la que deja libre el resto.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..models import LANE_POSITIONS, Position

# Peso según el orden de la posición en la lista de Riot (1.ª, 2.ª, 3.ª, 4.ª)
RANK_WEIGHTS = (1.0, 0.6, 0.35, 0.2)
# Peso de una posición que no está en su lista (posible, pero rara)
OFF_ROLE_WEIGHT = 0.03

PositionsLookup = Callable[[int], Sequence[Position]]
Probabilities = Mapping[int, Mapping[Position, float]]


@dataclass(frozen=True, slots=True)
class LaneGuess:
    champion_id: int
    position: Position
    probability: float


def _weights(champion_id: int, positions_for: PositionsLookup) -> dict[Position, float]:
    usual = positions_for(champion_id)
    if not usual:  # sin datos: cualquier línea es igual de probable
        return dict.fromkeys(LANE_POSITIONS, 1.0)
    weights = dict.fromkeys(LANE_POSITIONS, OFF_ROLE_WEIGHT)
    for rank, position in enumerate(usual):
        if position in weights:
            weights[position] = max(
                weights[position], RANK_WEIGHTS[min(rank, len(RANK_WEIGHTS) - 1)]
            )
    return weights


def infer_positions(
    champion_ids: Sequence[int], positions_for: PositionsLookup
) -> dict[int, dict[Position, float]]:
    """Probabilidad de que cada campeón juegue en cada línea."""
    champions = [c for c in dict.fromkeys(champion_ids) if c > 0][: len(LANE_POSITIONS)]
    if not champions:
        return {}
    weights = {c: _weights(c, positions_for) for c in champions}
    totals = {c: dict.fromkeys(LANE_POSITIONS, 0.0) for c in champions}
    grand_total = 0.0
    for assignment in itertools.permutations(LANE_POSITIONS, len(champions)):
        score = math.prod(weights[c][p] for c, p in zip(champions, assignment, strict=True))
        if score == 0:
            continue
        grand_total += score
        for champion, position in zip(champions, assignment, strict=True):
            totals[champion][position] += score
    if grand_total == 0:
        return {c: dict.fromkeys(LANE_POSITIONS, 1 / len(LANE_POSITIONS)) for c in champions}
    return {c: {p: v / grand_total for p, v in row.items()} for c, row in totals.items()}


SUMMONERS_RIFT = 11


def resolve_my_position(
    assigned: Position, champion_id: int, map_id: int, positions_for: PositionsLookup
) -> Position:
    """Tu posición: la asignada; si no hay (a ciegas, práctica), la habitual de tu campeón.

    Fuera de la Grieta del Invocador (ARAM...) no hay posiciones.
    """
    if map_id != SUMMONERS_RIFT:
        return Position.NONE
    if assigned != Position.NONE:
        return assigned
    usual = positions_for(champion_id)
    return usual[0] if usual else Position.NONE


def lane_opponent(probabilities: Probabilities, position: Position) -> LaneGuess | None:
    """Enemigo más probable en ``position`` (tu rival de línea)."""
    if position == Position.NONE or not probabilities:
        return None
    champion_id, row = max(probabilities.items(), key=lambda item: item[1].get(position, 0.0))
    return LaneGuess(champion_id, position, row.get(position, 0.0))
