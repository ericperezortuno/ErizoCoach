"""Traduce los eventos crudos del cliente en estado y eventos de la aplicación."""

from __future__ import annotations

import logging

from .bus import EventBus
from .champselect.parser import parse_session
from .champselect.tracker import ChampSelectTracker
from .events import ClientDisconnected, GameflowPhaseChanged
from .gamedata.catalog import GameData
from .lcu import endpoints
from .lcu.client import LcuClient, LcuError, LcuHttpError
from .lcu.connector import LcuConnector
from .lcu.websocket import LcuEvent
from .models import ClientInfo, GameflowPhase, QueueInfo

log = logging.getLogger(__name__)

# Fases en las que aceptamos datos de selección de campeones (UNKNOWN = aún no lo sabemos).
_CHAMP_SELECT_PHASES = (GameflowPhase.CHAMP_SELECT, GameflowPhase.UNKNOWN)


class ClientMonitor:
    def __init__(
        self,
        bus: EventBus,
        connector: LcuConnector,
        gamedata: GameData,
        tracker: ChampSelectTracker,
    ) -> None:
        self._bus = bus
        self._connector = connector
        self._gamedata = gamedata
        self._tracker = tracker
        self._phase = GameflowPhase.UNKNOWN

        connector.on_connect(self._on_connect)
        connector.on_event(endpoints.GAMEFLOW_PHASE, self._on_gameflow_event)
        connector.on_event(endpoints.CHAMP_SELECT_SESSION, self._on_champ_select_event)
        bus.subscribe(ClientDisconnected, self._on_disconnected)

    @property
    def phase(self) -> GameflowPhase:
        return self._phase

    async def _on_connect(self, client: LcuClient, info: ClientInfo) -> None:
        try:
            await self._gamedata.load(client, info.version)
        except LcuError as exc:
            log.warning("No se pudieron cargar los datos de campeones: %s", exc)
        await self._set_phase(GameflowPhase.parse(await client.get(endpoints.GAMEFLOW_PHASE)))

    async def _on_gameflow_event(self, event: LcuEvent) -> None:
        if not event.deleted:
            await self._set_phase(GameflowPhase.parse(event.data))

    async def _set_phase(self, phase: GameflowPhase) -> None:
        if phase == self._phase:
            return
        previous, self._phase = self._phase, phase
        await self._bus.publish(GameflowPhaseChanged(previous, phase))
        if previous == GameflowPhase.CHAMP_SELECT:
            await self._tracker.end(phase)
        if phase == GameflowPhase.CHAMP_SELECT:
            await self._sync_champ_select()

    async def _sync_champ_select(self) -> None:
        """Lee la sesión actual sin esperar al siguiente evento (útil si abres la app a mitad)."""
        client = self._connector.client
        if client is None:
            return
        try:
            data = await client.get(endpoints.CHAMP_SELECT_SESSION)
        except LcuHttpError as exc:
            if exc.status != 404:  # 404 = la sesión aún no existe; llegará por evento
                log.debug("No se pudo leer la selección de campeones: %s", exc)
            return
        await self._apply_session(data)

    async def _on_champ_select_event(self, event: LcuEvent) -> None:
        if event.deleted or self._phase not in _CHAMP_SELECT_PHASES:
            return
        await self._apply_session(event.data)

    async def _apply_session(self, data: object) -> None:
        try:
            snapshot = parse_session(data)
        except ValueError as exc:
            log.debug("Sesión de selección de campeones ignorada: %s", exc)
            return
        if not self._tracker.active and self._tracker.queue is None:
            self._tracker.set_queue(await self._load_queue())
        await self._tracker.update(snapshot)

    async def _load_queue(self) -> QueueInfo | None:
        client = self._connector.client
        if client is None:
            return None
        try:
            return QueueInfo.from_gameflow_session(await client.get(endpoints.GAMEFLOW_SESSION))
        except LcuError as exc:
            log.debug("No se pudo leer la cola actual: %s", exc)
            return None

    def _on_disconnected(self, _event: ClientDisconnected) -> None:
        self._phase = GameflowPhase.UNKNOWN
        self._tracker.discard()
