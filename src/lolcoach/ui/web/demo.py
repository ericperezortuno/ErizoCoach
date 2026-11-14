"""Partida de demostración (``lolcoach --demo``) para ver la ventana sin jugar.

Usa el análisis y el consejero reales sobre una partida inventada: Ahri en medio
contra un Zed que va muy fuerte y una Soraka que cura mucho.
"""

from __future__ import annotations

from ...builds.archetypes import BuildSource
from ...builds.recommender import BuildRecommender
from ...bus import EventBus
from ...events import BuildReady, LiveGameUpdated
from ...gamedata.catalog import GameData
from ...ingame.advisor import advise
from ...ingame.analysis import analyze
from ...ingame.models import CHAOS, ORDER, LiveGame, LiveItem, LivePlayer
from ...lcu.client import LcuClient
from ...models import Position

AHRI, GAREN, LEE_SIN, JINX, THRESH = 103, 86, 64, 222, 412
DARIUS, GRAVES, ZED, KAISA, SORAKA = 122, 104, 238, 145, 16

# (campeón, posición, nivel, K, D, A, CS, objetos)
ALLIES = [
    (GAREN, Position.TOP, 10, 2, 3, 1, 98, [1054, 3047, 3078]),
    (LEE_SIN, Position.JUNGLE, 10, 3, 2, 5, 72, [1101, 3158, 6610]),
    (JINX, Position.BOTTOM, 9, 1, 2, 2, 104, [1055, 3006, 3032]),
    (THRESH, Position.UTILITY, 8, 0, 3, 4, 18, [3865, 3047, 3190]),
]
ENEMIES = [
    (DARIUS, Position.TOP, 11, 3, 1, 2, 110, [1055, 3047, 3078, 3053]),
    (GRAVES, Position.JUNGLE, 10, 2, 2, 3, 95, [1101, 3158, 6699]),
    (ZED, Position.MIDDLE, 12, 7, 1, 2, 121, [1055, 3158, 3142, 6697, 3133]),
    (KAISA, Position.BOTTOM, 9, 1, 3, 2, 102, [1055, 3006, 6672]),
    (SORAKA, Position.UTILITY, 8, 0, 2, 6, 20, [3865, 3158, 6617]),
]


def _player(gd: GameData, row: tuple, team: str) -> LivePlayer:
    champion_id, position, level, kills, deaths, assists, cs, items = row
    champion = gd.champion(champion_id)
    return LivePlayer(
        champion_alias=champion.alias if champion else "",
        champion_name=gd.champion_name(champion_id),
        team=team,
        position=position,
        level=level,
        kills=kills,
        deaths=deaths,
        assists=assists,
        creep_score=cs,
        items=tuple(LiveItem(i, slot=n) for n, i in enumerate(items)),
    )


async def publish_demo(
    bus: EventBus, gd: GameData, client: LcuClient, source: BuildSource | None = None
) -> None:
    enemy_ids = [row[0] for row in ENEMIES]
    plan = await BuildRecommender(gd, source).recommend_for(
        client, AHRI, Position.MIDDLE, enemy_ids, 11
    )
    if plan is None:
        return
    await bus.publish(BuildReady(plan))
    me_items = [1056, plan.boots or 3020, plan.core[0], 1058]  # Doran, botas, 1.er objeto, Vara
    me = _player(gd, (AHRI, Position.MIDDLE, 10, 3, 4, 2, 112, me_items), ORDER)
    game = LiveGame(
        game_time=14 * 60 + 32,
        game_mode="CLASSIC",
        map_id=11,
        current_gold=1240,
        me=me,
        allies=tuple(_player(gd, row, ORDER) for row in ALLIES),
        enemies=tuple(_player(gd, row, CHAOS) for row in ENEMIES),
    )
    ids = [AHRI, *(row[0] for row in ALLIES), *enemy_ids]
    profiles = await gd.profiles(client, ids)
    report = analyze(
        game,
        champion_id=gd.champion_id_by_alias,
        item_price=lambda item_id: item.price_total if (item := gd.item(item_id)) else 0,
        profiles=profiles,
        positions_for=gd.positions_for,
        my_position=Position.MIDDLE,
    )
    advice = advise(
        plan, report, me.item_ids, game.current_gold, gd.item, gd.champion_name,
        game_time=game.game_time,
    )  # fmt: skip
    await bus.publish(LiveGameUpdated(game, report, advice, plan))
