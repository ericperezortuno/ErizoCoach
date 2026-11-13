"""Orquesta la fase 3: calcula la build y la lleva a la tienda de la partida.

Igual que las runas: al hacerse el último pick (o si cambia tu campeón después),
se calcula la build y se escribe el conjunto de objetos «LoL Coach: <campeón>».
"""

from __future__ import annotations

import logging

from ..bus import EventBus
from ..champselect.models import ChampSelectSnapshot
from ..config import Settings
from ..events import (
    BuildApplied,
    BuildReady,
    ChampSelectEnded,
    ChampSelectStarted,
    ClientDisconnected,
    MyChampionChanged,
    PicksFinalized,
)
from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient, LcuError
from ..lcu.connector import LcuConnector
from ..loadout.positions import PositionHint
from ..models import QueueInfo
from .archetypes import BuildSource
from .itemset import ItemSetWriter, set_title
from .models import BuildPlan
from .recommender import BuildRecommender

log = logging.getLogger(__name__)


class BuildService:
    def __init__(
        self,
        bus: EventBus,
        connector: LcuConnector,
        gamedata: GameData,
        settings: Settings,
        *,
        source: BuildSource | None = None,
        position_hint: PositionHint | None = None,
    ) -> None:
        self._bus = bus
        self._connector = connector
        self._gamedata = gamedata
        self._settings = settings
        self._recommender = BuildRecommender(gamedata, source, position_hint)
        self._writer = ItemSetWriter()
        self._summoner_id = 0
        self._queue: QueueInfo | None = None
        self._finalized = False
        self._applied_champion = 0
        self.current_plan: BuildPlan | None = None  # la usará la fase 4 durante la partida

        bus.subscribe(ChampSelectStarted, self._on_started)
        bus.subscribe(MyChampionChanged, self._on_my_champion)
        bus.subscribe(PicksFinalized, self._on_finalized)
        bus.subscribe(ChampSelectEnded, self._on_ended)
        bus.subscribe(ClientDisconnected, self._on_disconnected)

    def _on_started(self, event: ChampSelectStarted) -> None:
        self._reset()
        self._queue = event.queue
        self.current_plan = None

    def _on_ended(self, _event: ChampSelectEnded) -> None:
        self._reset()  # se conserva current_plan: es la build de la partida que empieza

    def _on_disconnected(self, _event: ClientDisconnected) -> None:
        self._reset()
        self._summoner_id = 0

    async def _on_my_champion(self, event: MyChampionChanged) -> None:
        if self._finalized:
            await self._run(event.snapshot)

    async def _on_finalized(self, event: PicksFinalized) -> None:
        self._finalized = True
        self._queue = event.queue or self._queue
        await self._run(event.snapshot)

    async def _run(self, snapshot: ChampSelectSnapshot) -> None:
        client = self._connector.client
        me = snapshot.me
        if client is None or me is None or not me.has_champion:
            return
        if me.champion_id == self._applied_champion:
            return
        plan = await self._recommender.recommend(client, snapshot, self._queue)
        if plan is None:
            return
        self._applied_champion = me.champion_id
        self.current_plan = plan
        await self._bus.publish(BuildReady(plan))
        await self._bus.publish(await self._apply(client, plan))

    async def _apply(self, client: LcuClient, plan: BuildPlan) -> BuildApplied:
        if not self._settings.auto_item_set:
            return BuildApplied(plan, None, False)
        title = set_title(self._gamedata.champion_name(plan.champion_id))
        try:
            if not self._summoner_id:
                self._summoner_id = await self._writer.summoner_id(client)
            await self._writer.write(client, self._summoner_id, plan, title)
        except LcuError as exc:
            return BuildApplied(
                plan, None, False, (f"No se pudo poner la build en la tienda: {exc}",)
            )
        return BuildApplied(plan, title, True)

    def _reset(self) -> None:
        self._queue = None
        self._finalized = False
        self._applied_champion = 0
