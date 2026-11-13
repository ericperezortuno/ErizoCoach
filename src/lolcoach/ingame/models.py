"""Modelo de la partida en vivo (``/liveclientdata/allgamedata``).

Privacidad: el Riot ID de cada jugador solo se usa para saber cuál eres tú; no
se guarda en el modelo, no se muestra y no se escribe en los registros.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..models import Position
from ..util import as_dict, as_int, as_list, as_str

# Riot usa varios formatos para la clave interna del campeón según el campeón y el parche:
# "game_character_displayname_Ahri" (lo habitual) o "Character_Aatrox_Name".
RAW_NAME_PATTERNS = (
    re.compile(r"^game_character_displayname_(\w+)$"),
    re.compile(r"^Character_(\w+?)_Name$"),
)
ORDER, CHAOS = "ORDER", "CHAOS"


@dataclass(frozen=True, slots=True)
class LiveItem:
    item_id: int
    count: int = 1
    slot: int = 0


@dataclass(frozen=True, slots=True)
class LivePlayer:
    champion_alias: str  # clave interna ("Kaisa"), para enlazar con los datos del cliente
    champion_name: str  # nombre en tu idioma
    team: str  # ORDER (azul) o CHAOS (rojo)
    position: Position
    level: int
    kills: int
    deaths: int
    assists: int
    creep_score: int
    items: tuple[LiveItem, ...] = ()
    is_dead: bool = False
    respawn_timer: float = 0.0
    is_bot: bool = False

    @property
    def kda(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"

    @property
    def item_ids(self) -> tuple[int, ...]:
        return tuple(i.item_id for i in self.items)


@dataclass(frozen=True, slots=True)
class LiveGame:
    game_time: float  # segundos
    game_mode: str
    map_id: int
    current_gold: float  # tu oro sin gastar
    me: LivePlayer | None
    allies: tuple[LivePlayer, ...]  # sin incluirte
    enemies: tuple[LivePlayer, ...]

    @property
    def players(self) -> tuple[LivePlayer, ...]:
        return ((self.me,) if self.me else ()) + self.allies + self.enemies


def parse_all_game_data(data: Any) -> LiveGame:
    if not isinstance(data, Mapping):
        raise ValueError("Los datos de la partida no son un objeto JSON")
    active = as_dict(data.get("activePlayer"))
    my_ids = {
        as_str(active.get(key)) for key in ("riotId", "summonerName") if as_str(active.get(key))
    }

    me: LivePlayer | None = None
    others: list[LivePlayer] = []
    for raw in as_list(data.get("allPlayers")):
        if not isinstance(raw, Mapping):
            continue
        player = _parse_player(raw)
        is_me = bool(my_ids) and any(
            as_str(raw.get(key)) in my_ids for key in ("riotId", "summonerName")
        )
        if is_me and me is None:
            me = player
        else:
            others.append(player)

    my_team = me.team if me else ORDER
    game_data = as_dict(data.get("gameData"))
    return LiveGame(
        game_time=_as_float(game_data.get("gameTime")),
        game_mode=as_str(game_data.get("gameMode")),
        map_id=as_int(game_data.get("mapNumber")),
        current_gold=_as_float(active.get("currentGold")),
        me=me,
        allies=tuple(p for p in others if p.team == my_team),
        enemies=tuple(p for p in others if p.team != my_team),
    )


def champion_alias(raw: Mapping[str, Any]) -> str:
    """Clave interna del campeón ("Aatrox", "Kaisa"); si no se reconoce, su nombre visible.

    Se prueba con ``rawChampionName`` y ``rawSkinName`` (con aspecto por defecto tiene el
    mismo formato). El nombre visible es el último recurso: el catálogo también busca por
    nombre, así que "Kai'Sa" o "Aatrox" se siguen reconociendo.
    """
    for key in ("rawChampionName", "rawSkinName"):
        value = as_str(raw.get(key))
        for pattern in RAW_NAME_PATTERNS:
            if match := pattern.match(value):
                return match.group(1)
    return as_str(raw.get("championName"))


def _parse_player(raw: Mapping[str, Any]) -> LivePlayer:
    scores = as_dict(raw.get("scores"))
    alias = champion_alias(raw)
    items = tuple(
        LiveItem(
            item_id=as_int(item.get("itemID")),
            count=max(as_int(item.get("count"), 1), 1),
            slot=as_int(item.get("slot")),
        )
        for item in (as_dict(i) for i in as_list(raw.get("items")))
        if as_int(item.get("itemID")) > 0
    )
    return LivePlayer(
        champion_alias=alias,
        champion_name=as_str(raw.get("championName")),
        team=as_str(raw.get("team")) or ORDER,
        position=Position.parse(raw.get("position")),
        level=as_int(raw.get("level"), 1),
        kills=as_int(scores.get("kills")),
        deaths=as_int(scores.get("deaths")),
        assists=as_int(scores.get("assists")),
        creep_score=as_int(scores.get("creepScore")),
        items=items,
        is_dead=bool(raw.get("isDead")),
        respawn_timer=_as_float(raw.get("respawnTimer")),
        is_bot=bool(raw.get("isBot")),
    )


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0
