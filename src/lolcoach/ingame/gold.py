"""Oro total conseguido por cada jugador, como el de las retransmisiones de Riot.

En las partidas oficiales Riot muestra el oro **total conseguido** (gastado o no) y su
diferencia entre equipos, en directo. Ese dato sale del servidor de la partida y la API
local de la partida (Live Client Data) solo da el oro sin gastar de **tu** jugador: del resto
solo se ve lo mismo que en el marcador (objetos, KDA, súbditos, nivel) y los eventos de la
partida (torres, dragones...). Así que se calcula con todo lo que da oro y se puede ver:

* 500 de oro inicial y 20,4 de oro cada 10 s desde el 1:05 (igual para todos).
* Súbditos y monstruos: lo que vale de media cada uno (cambia con el tiempo de partida).
* Asesinatos: 300 de oro (a nivel 1-6) hasta 420 (a nivel 18), según el nivel del rival;
  +100 la primera sangre.
* Asistencias: la mitad de la recompensa del asesinato, repartida entre los que ayudan.
* Torres: el oro global que reciben todos los del equipo que la tira.

Lo que no se puede ver (placas de torre, recompensas por racha y por ir por detrás,
oro de los objetos de apoyo, dragones, Barón...) se corrige solo: el valor de los objetos
de un jugador nunca puede ser mayor que el oro que ha conseguido, así que cada vez que
alguien compra, lo que faltaba se suma a su cálculo para el resto de la partida. Tu oro es
exacto: tus objetos más tu oro sin gastar.

Valores de la wiki oficial de LoL (parches 26.01-26.03).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..models import Position
from .models import CHAOS, ORDER, LiveGame, LivePlayer

STARTING_GOLD = 500
PASSIVE_START = 65.0  # 1:05
PASSIVE_PER_SECOND = 2.04  # 20,4 cada 10 s
# Súbditos: 18,7 de media por súbdito hasta el 15:00 (oleadas de 3 cuerpo a cuerpo de 20,
# 3 a distancia de 14 y un cañón de vez en cuando); ~21,5 desde el 25:00, con más cañones.
LANE_CS_GOLD_EARLY, LANE_CS_GOLD_LATE = 18.7, 21.5
LANE_CS_EARLY_UNTIL, LANE_CS_LATE_FROM = 900.0, 1500.0
JUNGLE_CS_GOLD = 24.0  # monstruos de la jungla, de media (prudente: si falta, se corrige)
KILL_BOUNTY_MIN_LEVEL = 6  # 300 de oro hasta nivel 6...
KILL_BOUNTY_BASE, KILL_BOUNTY_PER_LEVEL = 300, 10  # ...y 10 más por nivel, hasta 420 a nivel 18
FIRST_BLOOD_BONUS = 100
# Torres: oro global para cada jugador del equipo que la destruye
TURRET_GLOBAL_GOLD = {"outer": 50, "inner": 25, "inhibitor": 25, "nexus": 50}
# Turret_T1_L_03_A: T1 = torre del equipo azul (ORDER), T2 = rojo (CHAOS); L/R (arriba y
# abajo): 03 exterior, 02 interior, 01 inhibidor; C (centro): 05, 04, 03, y 02/01 del nexo.
TURRET_NAME = re.compile(r"^Turret_T([12])_([LCR])_(\d\d)_A$")
TURRET_TIERS = {
    "L": {"03": "outer", "02": "inner", "01": "inhibitor"},
    "R": {"03": "outer", "02": "inner", "01": "inhibitor"},
    "C": {"05": "outer", "04": "inner", "03": "inhibitor", "02": "nexus", "01": "nexus"},
}
RESTART_TOLERANCE = 5.0  # segundos: si el reloj va hacia atrás más que esto, es otra partida


def passive_gold(game_time: float) -> float:
    """Oro inicial y pasivo: el mismo para los diez jugadores."""
    return STARTING_GOLD + PASSIVE_PER_SECOND * max(game_time - PASSIVE_START, 0.0)


def lane_cs_gold(game_time: float) -> float:
    """Lo que vale de media un súbdito en este momento de la partida."""
    if game_time <= LANE_CS_EARLY_UNTIL:
        return LANE_CS_GOLD_EARLY
    if game_time >= LANE_CS_LATE_FROM:
        return LANE_CS_GOLD_LATE
    share = (game_time - LANE_CS_EARLY_UNTIL) / (LANE_CS_LATE_FROM - LANE_CS_EARLY_UNTIL)
    return LANE_CS_GOLD_EARLY + share * (LANE_CS_GOLD_LATE - LANE_CS_GOLD_EARLY)


def kill_bounty(level: float) -> float:
    """Oro base por matar a un campeón de ese nivel (300 a nivel 1-6, 420 a nivel 18)."""
    extra = max(min(level, 18) - KILL_BOUNTY_MIN_LEVEL, 0)
    return KILL_BOUNTY_BASE + KILL_BOUNTY_PER_LEVEL * extra


def turret_gold(destroyed: Iterable[str]) -> dict[str, int]:
    """Oro global de torres que ha recibido cada jugador de cada equipo."""
    gold = {ORDER: 0, CHAOS: 0}
    for name in set(destroyed):
        match = TURRET_NAME.match(name)
        if match is None:
            continue
        owner, lane, index = match.groups()
        tier = TURRET_TIERS[lane].get(index)
        if tier is None:
            continue
        winner = CHAOS if owner == "1" else ORDER  # la recibe el equipo contrario
        gold[winner] += TURRET_GLOBAL_GOLD[tier]
    return gold


def is_jungler(player: LivePlayer) -> bool:
    return player.position == Position.JUNGLE or player.has_smite


@dataclass
class _Seen:
    """Lo que se lleva sumado de un jugador y lo último que se le vio."""

    earned: float  # súbditos, asesinatos, asistencias y primera sangre
    kills: int
    assists: int
    creep_score: int
    correction: float = 0.0  # lo que el cálculo no ve y han delatado sus objetos


class GoldTracker:
    """Calcula el oro total de cada jugador en cada lectura de la partida.

    Hay que llamar a ``update`` con cada lectura, en orden: lo que sube de una lectura a la
    siguiente (súbditos, asesinatos...) se valora con el momento en que pasó.
    """

    def __init__(self, item_value: Callable[[LivePlayer], int]) -> None:
        self._item_value = item_value
        self._players: dict[tuple[str, str, int], _Seen] = {}
        self._last_time = -1.0
        self._kills_so_far: int | None = None  # None: aún no se ha visto la partida

    def update(self, game: LiveGame) -> dict[int, int]:
        """Oro total de cada jugador (clave: ``id(jugador)``)."""
        if game.game_time + RESTART_TOLERANCE < self._last_time:
            self._players.clear()  # otra partida (o se ha vuelto a cargar)
            self._kills_so_far = None
        self._last_time = game.game_time
        players = game.players
        seen_before = [self._players.get(key) for key in _keys(players)]
        levels = {team: _average_level(players, team) for team in (ORDER, CHAOS)}
        new_kills = dict.fromkeys((ORDER, CHAOS), 0)
        new_assists = dict.fromkeys((ORDER, CHAOS), 0)
        for player, seen in zip(players, seen_before, strict=True):
            if seen is not None:
                new_kills[player.team] += max(player.kills - seen.kills, 0)
                new_assists[player.team] += max(player.assists - seen.assists, 0)
        first_blood = self._first_blood(players, seen_before)

        towers = turret_gold(game.destroyed_turrets)
        result: dict[int, int] = {}
        for key, player, seen in zip(_keys(players), players, seen_before, strict=True):
            bounty = kill_bounty(levels[_other(player.team)])
            if seen is None:
                seen = self._players[key] = _first_look(player, players, game.game_time, bounty)
            else:
                share = _assist_share(new_kills[player.team], new_assists[player.team], bounty)
                seen.earned += (
                    max(player.creep_score - seen.creep_score, 0) * _cs_gold(player, game.game_time)
                    + max(player.kills - seen.kills, 0) * bounty
                    + max(player.assists - seen.assists, 0) * share
                    + (FIRST_BLOOD_BONUS if player is first_blood else 0)
                )
                seen.kills, seen.assists = player.kills, player.assists
                seen.creep_score = player.creep_score
            model = passive_gold(game.game_time) + towers.get(player.team, 0) + seen.earned
            observed = self._item_value(player)
            if player is game.me:
                observed += round(game.current_gold)  # tu oro: exacto
            seen.correction = max(seen.correction, observed - model)
            result[id(player)] = round(max(observed, model + seen.correction))
        return result

    def _first_blood(
        self, players: tuple[LivePlayer, ...], seen_before: list[_Seen | None]
    ) -> LivePlayer | None:
        """Quién acaba de hacer la primera sangre (si se ha visto pasar y solo mató uno)."""
        total = sum(p.kills for p in players)
        before, self._kills_so_far = self._kills_so_far, total
        if before != 0 or total == 0:
            return None  # ya la hubo, o se abrió la app con asesinatos ya hechos
        killers = [
            p for p, seen in zip(players, seen_before, strict=True)
            if seen is not None and p.kills > seen.kills
        ]  # fmt: skip
        return killers[0] if len(killers) == 1 else None


def _keys(players: Iterable[LivePlayer]) -> list[tuple[str, str, int]]:
    """Clave de cada jugador: equipo, campeón y cuántos del mismo campeón van antes."""
    count: dict[tuple[str, str], int] = {}
    keys = []
    for player in players:
        base = (player.team, player.champion_alias)
        count[base] = count.get(base, 0) + 1
        keys.append((*base, count[base]))
    return keys


def _first_look(
    player: LivePlayer, players: tuple[LivePlayer, ...], game_time: float, bounty: float
) -> _Seen:
    """Primera vez que se ve al jugador (p. ej., app abierta a mitad de partida): todo lo que
    lleva se valora de media (los primeros asesinatos fueron a niveles más bajos)."""
    mates = [p for p in players if p.team == player.team]
    kills = sum(p.kills for p in mates)
    assists = sum(p.assists for p in mates)
    early_bounty = (KILL_BOUNTY_BASE + bounty) / 2
    share = _assist_share(kills, assists, early_bounty)
    if is_jungler(player):
        cs_gold = JUNGLE_CS_GOLD
    else:
        cs_gold = (lane_cs_gold(0) + lane_cs_gold(game_time)) / 2
    earned = player.creep_score * cs_gold + player.kills * early_bounty + player.assists * share
    return _Seen(earned, player.kills, player.assists, player.creep_score)


def _assist_share(kills: int, assists: int, bounty: float) -> float:
    """Oro por asistencia: cada asesinato con ayuda reparte la mitad de su recompensa entre
    los que ayudan. Con los asesinatos y asistencias nuevos del equipo se sabe cuántos
    ayudaron de media."""
    if assists <= 0:
        return 0.0
    with_help = min(kills, assists) if kills else assists / 2  # sin datos: ~2 por asesinato
    return bounty / 2 * with_help / assists


def _cs_gold(player: LivePlayer, game_time: float) -> float:
    return JUNGLE_CS_GOLD if is_jungler(player) else lane_cs_gold(game_time)


def _average_level(players: Iterable[LivePlayer], team: str) -> float:
    levels = [p.level for p in players if p.team == team]
    return sum(levels) / len(levels) if levels else 1.0


def _other(team: str) -> str:
    return CHAOS if team == ORDER else ORDER
