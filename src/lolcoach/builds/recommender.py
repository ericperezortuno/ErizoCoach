"""Calcula la build para tu partida: base de tu campeón + adaptación a los enemigos."""

from __future__ import annotations

import dataclasses
import logging

from ..champselect.models import ChampSelectSnapshot
from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient
from ..loadout.positions import resolve_my_position
from ..models import QueueInfo
from .archetypes import HOWLING_ABYSS, SUMMONERS_RIFT, BuildSource, RuleBasedSource
from .models import BaseBuild, BuildPlan, ItemCount
from .rules import BuildContext, Enemy, adapt_build

log = logging.getLogger(__name__)

SUPPORTED_MAPS = (SUMMONERS_RIFT, HOWLING_ABYSS)


class BuildRecommender:
    def __init__(self, gamedata: GameData, source: BuildSource | None = None) -> None:
        self._gamedata = gamedata
        self._source = source or RuleBasedSource()

    async def recommend(
        self, client: LcuClient, snapshot: ChampSelectSnapshot, queue: QueueInfo | None
    ) -> BuildPlan | None:
        me = snapshot.me
        if me is None or not me.has_champion:
            return None
        gd = self._gamedata
        map_id = queue.map_id if queue and queue.map_id else SUMMONERS_RIFT
        if map_id not in SUPPORTED_MAPS:
            return None  # Arena y otros modos no usan conjuntos de objetos normales
        position = resolve_my_position(me.position, me.champion_id, map_id, gd.positions_for)

        enemy_ids = [slot.champion_id for slot in snapshot.known_enemies]
        profiles = await gd.profiles(client, [me.champion_id, *enemy_ids])
        champion = gd.champion(me.champion_id)
        base = self._validated(
            self._source.base_build(champion, profiles.get(me.champion_id), position, map_id)
        )
        enemies = tuple(Enemy(c, _alias(gd, c), profiles.get(c)) for c in enemy_ids)
        ctx = BuildContext(base.archetype, position, map_id, enemies)
        plan = adapt_build(base, ctx, me.champion_id, gd)
        return dataclasses.replace(plan, notes=_notes(ctx))

    def _validated(self, base: BaseBuild) -> BaseBuild:
        """Quita lo que no exista o no se pueda comprar en este parche y rellena el núcleo."""
        gd = self._gamedata

        def ok(item_id: int) -> bool:
            if gd.is_buyable(item_id):
                return True
            log.debug("Objeto %s descartado: no se puede comprar en este parche", item_id)
            return False

        core = [i for i in base.core if ok(i)]
        extra = [i for i in base.extra if ok(i) and i not in core]
        while len(core) < len(base.core) and extra:
            core.append(extra.pop(0))
        boots = base.boots if base.boots is not None and ok(base.boots) else None
        upgrade = base.support_upgrade
        if upgrade is not None and gd.item(upgrade) is None:
            upgrade = None  # las mejoras de apoyo no se venden en la tienda: basta con que existan
        return dataclasses.replace(
            base,
            starting=tuple(s for s in base.starting if ok(s.item_id)) or (ItemCount(2003),),
            boots=boots,
            core=tuple(core),
            extra=tuple(extra),
            support_upgrade=upgrade,
        )


def _alias(gd: GameData, champion_id: int) -> str:
    champion = gd.champion(champion_id)
    return champion.alias if champion else ""


def _notes(ctx: BuildContext) -> tuple[str, ...]:
    if not ctx.known:
        return ("Enemigos no visibles: build base de tu campeón, sin adaptar a los rivales.",)
    return ()
