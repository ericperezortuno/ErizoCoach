"""Datos de partida en vivo con el formato documentado de ``/liveclientdata/allgamedata``."""

from __future__ import annotations

from typing import Any

ME_RIOT_ID = "Jugador#EUW"


def live_player(
    alias: str,
    *,
    team: str = "ORDER",
    position: str = "",
    level: int = 6,
    kda: tuple[int, int, int] = (0, 0, 0),
    cs: int = 50,
    items: tuple[int, ...] = (),
    riot_id: str = "",
    dead: bool = False,
) -> dict[str, Any]:
    riot_id = riot_id or f"{alias}Player#EUW"
    return {
        "championName": alias,
        "isBot": False,
        "isDead": dead,
        "items": [
            {"canUse": False, "consumable": False, "count": 1, "displayName": str(item),
             "itemID": item, "price": 0, "slot": slot}
            for slot, item in enumerate(items)
        ],
        "level": level,
        "position": position,
        "rawChampionName": f"game_character_displayname_{alias}",
        "respawnTimer": 12.5 if dead else 0.0,
        "riotId": riot_id,
        "riotIdGameName": riot_id.split("#")[0],
        "riotIdTagLine": "EUW",
        "scores": {"assists": kda[2], "creepScore": cs, "deaths": kda[1], "kills": kda[0],
                   "wardScore": 0.0},
        "skinID": 0,
        "summonerName": riot_id,
        "team": team,
    }  # fmt: skip


def all_game_data(
    me: dict[str, Any], others: list[dict[str, Any]], *, game_time: float = 900, gold: float = 500
) -> dict[str, Any]:
    return {
        "activePlayer": {
            "currentGold": gold,
            "level": me["level"],
            "riotId": me["riotId"],
            "summonerName": me["summonerName"],
            "championStats": {},
        },
        "allPlayers": [me, *others],
        "events": {"Events": [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.05}]},
        "gameData": {"gameMode": "CLASSIC", "gameTime": game_time, "mapName": "Map11",
                     "mapNumber": 11, "mapTerrain": "Default"},
    }  # fmt: skip


def bot_game(
    my_items: tuple[int, ...],
    *,
    gold: float = 500,
    game_time: float = 900,
    position: str = "BOTTOM",
) -> dict[str, Any]:
    """Kai'Sa (tú, línea inferior) contra Darius, Graves, Zed, Jhin y Soraka."""
    me = live_player("Kaisa", position=position, items=my_items, riot_id=ME_RIOT_ID)
    allies = [
        live_player("Garen", position="TOP", items=(1054, 3047)),
        live_player("LeeSin", position="JUNGLE", items=(1101, 3158)),
        live_player("Ahri", position="MIDDLE", items=(1056, 3020)),
        live_player("Thresh", position="UTILITY", items=(3865,)),
    ]
    enemies = [
        live_player("Darius", team="CHAOS", position="TOP", items=(1055, 3047)),
        live_player("Graves", team="CHAOS", position="JUNGLE", items=(1101, 3158)),
        live_player("Zed", team="CHAOS", position="MIDDLE", items=(1055, 3158)),
        live_player("Jhin", team="CHAOS", position="BOTTOM", items=(1055,)),
        live_player("Soraka", team="CHAOS", position="UTILITY", items=(3865,)),
    ]
    return all_game_data(me, allies + enemies, game_time=game_time, gold=gold)


def standard_game(
    *,
    zed_kda: tuple[int, int, int] = (2, 2, 1),
    zed_items: tuple[int, ...] = (1055, 3158),
    my_items: tuple[int, ...] = (1056, 3020),
    game_time: float = 900,
    gold: float = 500,
) -> dict[str, Any]:
    """Ahri (tú, medio) contra Darius, Graves, Zed, Kai'Sa y Soraka."""
    me = live_player("Ahri", position="MIDDLE", items=my_items, riot_id=ME_RIOT_ID, kda=(1, 1, 1))
    allies = [
        live_player("Garen", position="TOP", items=(1054, 3047)),
        live_player("LeeSin", position="JUNGLE", items=(1101, 3158)),
        live_player("Jinx", position="BOTTOM", items=(1055, 3006)),
        live_player("Thresh", position="UTILITY", items=(3865,)),
    ]
    enemies = [
        live_player("Darius", team="CHAOS", position="TOP", items=(1055, 3047)),
        live_player("Graves", team="CHAOS", position="JUNGLE", items=(1101, 3158)),
        live_player("Zed", team="CHAOS", position="MIDDLE", items=zed_items, kda=zed_kda),
        live_player("Kaisa", team="CHAOS", position="BOTTOM", items=(1055, 3006)),
        live_player("Soraka", team="CHAOS", position="UTILITY", items=(3865,)),
    ]
    return all_game_data(me, allies + enemies, game_time=game_time, gold=gold)
