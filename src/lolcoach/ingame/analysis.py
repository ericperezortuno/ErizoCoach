"""¿Quién va fuerte? Análisis a partir de lo que se ve en el marcador.

El oro de los rivales no es visible, pero sus objetos sí: el valor de sus objetos
es una buena aproximación del oro que han conseguido. Se combina con el KDA y el
nivel, comparando con la media de la partida:

    puntuación = 0,60 · oro en objetos + 0,25 · KDA + 0,15 · nivel   (cada término de -1 a 1)

En los primeros minutos todo el mundo aparece como «normal»: no hay datos suficientes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..gamedata.catalog import ChampionProfile, DamageType
from ..loadout.positions import infer_positions, lane_opponent
from ..models import Position
from .models import LiveGame, LivePlayer

EARLY_GAME_SECONDS = 180
GOLD_WEIGHT, KDA_WEIGHT, LEVEL_WEIGHT = 0.60, 0.25, 0.15
MIN_GOLD_SCALE = 1500  # evita que pequeñas diferencias al principio parezcan enormes


class Strength(StrEnum):
    VERY_STRONG = "very_strong"
    STRONG = "strong"
    EVEN = "even"
    BEHIND = "behind"


STRENGTH_LABELS = {
    Strength.VERY_STRONG: "Muy fuerte",
    Strength.STRONG: "Fuerte",
    Strength.EVEN: "Normal",
    Strength.BEHIND: "Por detrás",
}
VERY_STRONG_SCORE, STRONG_SCORE, BEHIND_SCORE = 0.30, 0.12, -0.15


@dataclass(frozen=True, slots=True)
class PlayerReport:
    player: LivePlayer
    champion_id: int
    item_gold: int
    gold_vs_average: int  # positivo: más oro en objetos que la media
    score: float
    strength: Strength
    damage_type: DamageType
    is_lane_opponent: bool = False
    damage_level: int = 2  # 0-3 según su perfil: un apoyo encantador no es una amenaza de daño

    @property
    def threatening(self) -> bool:
        return self.strength in (Strength.VERY_STRONG, Strength.STRONG)


@dataclass(frozen=True, slots=True)
class GameReport:
    me: PlayerReport | None
    allies: tuple[PlayerReport, ...]
    enemies: tuple[PlayerReport, ...]  # de más a menos peligroso

    @property
    def top_threat(self) -> PlayerReport | None:
        return self.enemies[0] if self.enemies and self.enemies[0].threatening else None


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def analyze(
    game: LiveGame,
    *,
    champion_id: Callable[[str], int],
    item_price: Callable[[int], int],
    profiles: Mapping[int, ChampionProfile],
    positions_for: Callable[[int], tuple[Position, ...]],
    my_position: Position = Position.NONE,
) -> GameReport:
    players = game.players
    if not players:
        return GameReport(None, (), ())
    gold = {id(p): sum(item_price(i.item_id) * i.count for i in p.items) for p in players}
    average_gold = sum(gold.values()) / len(players)
    average_level = sum(p.level for p in players) / len(players)
    scale = max(average_gold, MIN_GOLD_SCALE)
    early = game.game_time < EARLY_GAME_SECONDS

    lane_rival = _lane_rival(game, champion_id, positions_for, my_position)

    def report(player: LivePlayer) -> PlayerReport:
        cid = champion_id(player.champion_alias)
        item_gold = gold[id(player)]
        kda = _clamp((player.kills - player.deaths + 0.3 * player.assists) / 6)
        level = _clamp((player.level - average_level) / 3)
        score = (
            GOLD_WEIGHT * _clamp((item_gold - average_gold) / scale)
            + KDA_WEIGHT * kda
            + LEVEL_WEIGHT * level
        )
        if early and player.kills < 2:
            strength = Strength.EVEN
        elif score >= VERY_STRONG_SCORE:
            strength = Strength.VERY_STRONG
        elif score >= STRONG_SCORE:
            strength = Strength.STRONG
        elif score <= BEHIND_SCORE:
            strength = Strength.BEHIND
        else:
            strength = Strength.EVEN
        profile = profiles.get(cid)
        return PlayerReport(
            player=player,
            champion_id=cid,
            item_gold=item_gold,
            gold_vs_average=round(item_gold - average_gold),
            score=round(score, 3),
            strength=strength,
            damage_type=profile.damage_type if profile else DamageType.UNKNOWN,
            is_lane_opponent=player is lane_rival,
            damage_level=profile.damage if profile else 2,
        )

    enemies = sorted((report(p) for p in game.enemies), key=lambda r: -r.score)
    allies = tuple(report(p) for p in game.allies)
    return GameReport(report(game.me) if game.me else None, allies, tuple(enemies))


def _lane_rival(
    game: LiveGame,
    champion_id: Callable[[str], int],
    positions_for: Callable[[int], tuple[Position, ...]],
    my_position: Position,
) -> LivePlayer | None:
    """Tu rival de línea: el que el juego dice que juega en tu posición; si no, por probabilidad."""
    position = game.me.position if game.me and game.me.position else my_position
    if position == Position.NONE:
        return None
    same = [p for p in game.enemies if p.position == position]
    if len(same) == 1:
        return same[0]
    by_id = {champion_id(p.champion_alias): p for p in game.enemies}
    guess = lane_opponent(infer_positions(list(by_id), positions_for), position)
    return by_id.get(guess.champion_id) if guess and guess.probability >= 0.6 else None
