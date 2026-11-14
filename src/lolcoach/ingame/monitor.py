"""Sigue la partida en curso: lee el marcador cada pocos segundos, analiza y aconseja.

* Arranca solo al empezar la partida (o al abrir la app con una partida en curso).
* Se detiene al terminar.
* Si la partida aún está cargando, espera sin hacer ruido.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import Callable

from ..builds.archetypes import BuildSource
from ..builds.models import BuildPlan
from ..builds.recommender import BuildRecommender
from ..bus import EventBus
from ..events import ClientDisconnected, GameflowPhaseChanged, LiveGameEnded, LiveGameUpdated
from ..gamedata.catalog import GameData
from ..lcu.client import LcuError
from ..lcu.connector import LcuConnector
from ..loadout.positions import PositionHint
from ..models import GameflowPhase, Position
from .advisor import advise
from .analysis import analyze, item_value
from .client import LiveClient, LiveClientUnavailable
from .gold import GoldTracker
from .models import LiveGame, parse_all_game_data
from .role_slot import RoleSlotBoots

log = logging.getLogger(__name__)

IN_GAME = frozenset({GameflowPhase.GAME_START, GameflowPhase.IN_PROGRESS, GameflowPhase.RECONNECT})
DEFAULT_POLL_INTERVAL = 1.0  # cada segundo, como el oro de las retransmisiones


class LiveGameMonitor:
    def __init__(
        self,
        bus: EventBus,
        connector: LcuConnector,
        gamedata: GameData,
        ssl_context: ssl.SSLContext,
        current_plan: Callable[[], BuildPlan | None],
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        live_client_factory: Callable[[], LiveClient] | None = None,
        source: BuildSource | None = None,
        position_hint: PositionHint | None = None,
    ) -> None:
        self._bus = bus
        self._connector = connector
        self._gamedata = gamedata
        self._current_plan = current_plan
        self._poll_interval = poll_interval
        self._client_factory = live_client_factory or (lambda: LiveClient(ssl_context))
        self._source = source
        self._recommender = BuildRecommender(gamedata, source, position_hint)
        self._task: asyncio.Task[None] | None = None
        self._plan: BuildPlan | None = None
        self._plan_revision = 0  # versión de las estadísticas con la que se calculó ``_plan``
        self._role_boots: RoleSlotBoots | None = None  # botas que la API no muestra
        self._threat_id = 0  # enemigo al que respondía el último consejo (para no alternar)
        self._gold = GoldTracker(lambda p: item_value(p, self._item_price))

        bus.subscribe(GameflowPhaseChanged, self._on_phase)
        bus.subscribe(ClientDisconnected, self._on_disconnected)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _on_phase(self, event: GameflowPhaseChanged) -> None:
        if event.current in IN_GAME:
            if not self.running:
                self._plan = None
                self._role_boots = None  # partida nueva: se empieza de cero
                self._threat_id = 0
                self._gold = GoldTracker(lambda p: item_value(p, self._item_price))
                self._task = asyncio.create_task(self._run())
        elif self.running:
            await self.stop()

    async def _on_disconnected(self, _event: ClientDisconnected) -> None:
        if self.running:
            await self.stop()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._bus.publish(LiveGameEnded())

    async def _run(self) -> None:
        log.debug("Siguiendo la partida en curso")
        async with self._client_factory() as live:
            while True:
                try:
                    game = parse_all_game_data(await live.all_game_data())
                except LiveClientUnavailable as exc:
                    log.debug("Partida aún no disponible: %s", exc)
                except ValueError as exc:
                    log.debug("Datos de partida ignorados: %s", exc)
                else:
                    try:
                        await self._bus.publish(await self._update(game))
                    except Exception:
                        log.exception("Error analizando la partida")
                await asyncio.sleep(self._poll_interval)

    async def _update(self, game: LiveGame) -> LiveGameUpdated:
        gd = self._gamedata
        client = self._connector.client
        my_id = gd.champion_id_by_alias(game.me.champion_alias) if game.me else 0
        enemy_ids = [gd.champion_id_by_alias(p.champion_alias) for p in game.enemies]
        ids = [c for c in (my_id, *enemy_ids) if c > 0]
        profiles = await gd.profiles(client, ids) if client is not None else {}

        plan = await self._plan_for(my_id, game, enemy_ids)
        report = analyze(
            game,
            champion_id=gd.champion_id_by_alias,
            item_price=self._item_price,
            profiles=profiles,
            positions_for=gd.positions_for,
            my_position=plan.position if plan else Position.NONE,
            total_gold=self._gold.update(game),
        )
        hidden_boots = False
        if game.me is not None:
            if self._role_boots is None:
                self._role_boots = RoleSlotBoots(gd.item)
            hidden_boots = self._role_boots.update(game.me, game.current_gold)
        advice = None
        if plan is not None and game.me is not None:
            advice = advise(
                plan, report, game.me.item_ids, game.current_gold, gd.item, gd.champion_name,
                game_time=game.game_time, boots_out_of_sight=hidden_boots,
                keep_threat=self._threat_id,
            )  # fmt: skip
            self._threat_id = advice.threat_id
        return LiveGameUpdated(game, report, advice, plan)

    def _item_price(self, item_id: int) -> int:
        item = self._gamedata.item(item_id)
        return item.price_total if item else 0

    async def _plan_for(self, my_id: int, game: LiveGame, enemy_ids: list[int]) -> BuildPlan | None:
        """La build de la selección de campeones; si no la hay (app abierta a mitad), se calcula."""
        current = self._current_plan()
        if current is not None and current.champion_id == my_id:
            return current
        # Se recalcula si cambian las estadísticas: al abrir la app a mitad de partida, la
        # primera build sale antes de que terminen de leerse (y sería la de las reglas).
        revision = getattr(self._source, "revision", 0)
        if (
            self._plan is not None
            and self._plan.champion_id == my_id
            and self._plan_revision == revision
        ):
            return self._plan
        client = self._connector.client
        if client is None or my_id <= 0:
            return None
        position = game.me.position if game.me else Position.NONE
        self._plan_revision = revision
        try:
            self._plan = await self._recommender.recommend_for(
                client, my_id, position, [c for c in enemy_ids if c > 0], game.map_id or 11
            )
        except LcuError as exc:
            log.debug("No se pudo calcular la build en partida: %s", exc)
            return None
        return self._plan
