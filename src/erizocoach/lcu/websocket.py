"""Eventos en tiempo real del cliente (websocket con protocolo WAMP).

En lugar de preguntar al cliente cada segundo ("¿ha cambiado algo?"), nos
suscribimos solo a las rutas que interesan y el cliente nos avisa al instante.
Menos CPU, menos tráfico y reacción inmediata.

El websocket usa su propia conexión: el cliente rechaza (HTTP 404) la apertura
del websocket sobre una conexión que ya se ha usado para peticiones normales.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import aiohttp

from .client import LcuClient, LcuTlsError, LcuUnavailable, describe_error, open_session

log = logging.getLogger(__name__)

WAMP_SUBSCRIBE = 5
WAMP_EVENT = 8


def topic_for(uri: str) -> str:
    """``/lol-champ-select/v1/session`` -> ``OnJsonApiEvent_lol-champ-select_v1_session``."""
    return "OnJsonApiEvent" + uri.replace("/", "_")


@dataclass(frozen=True, slots=True)
class LcuEvent:
    uri: str
    event_type: str  # "Create" | "Update" | "Delete"
    data: Any

    @property
    def deleted(self) -> bool:
        return self.event_type == "Delete"


def parse_message(raw: str) -> LcuEvent | None:
    """Convierte un mensaje WAMP ``[8, topic, {uri, eventType, data}]`` en ``LcuEvent``."""
    if not raw:
        return None
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(message, list) or len(message) < 3 or message[0] != WAMP_EVENT:
        return None
    payload = message[2]
    if not isinstance(payload, dict):
        return None
    uri = payload.get("uri")
    event_type = payload.get("eventType")
    if not isinstance(uri, str) or not isinstance(event_type, str):
        return None
    return LcuEvent(uri=uri, event_type=event_type, data=payload.get("data"))


class LcuEventStream:
    """Websocket suscrito a ``uris``. Se itera con ``async for event in stream``.

    La iteración termina cuando el cliente cierra la conexión.
    """

    def __init__(self, client: LcuClient, uris: Iterable[str]) -> None:
        self._credentials = client.credentials
        self._ssl_context = client.ssl_context
        self._uris = frozenset(uris)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def __aenter__(self) -> Self:
        self._session = open_session(self._credentials, self._ssl_context)
        try:
            self._ws = await self._session.ws_connect("/", protocols=("wamp",))
            for uri in sorted(self._uris):
                await self._ws.send_str(json.dumps([WAMP_SUBSCRIBE, topic_for(uri)]))
        except aiohttp.ClientSSLError as exc:
            await self._close()
            raise LcuTlsError(f"certificado no válido: {describe_error(exc)}") from exc
        except (aiohttp.ClientError, ConnectionError, TimeoutError) as exc:
            await self._close()
            raise LcuUnavailable(f"no se pudo abrir el websocket: {describe_error(exc)}") from exc
        log.debug("Suscrito a %d rutas de eventos", len(self._uris))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._close()

    async def _close(self) -> None:
        ws, self._ws = self._ws, None
        session, self._session = self._session, None
        if ws is not None and not ws.closed:
            await ws.close()
        if session is not None:
            await session.close()

    def __aiter__(self) -> AsyncIterator[LcuEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[LcuEvent]:
        ws = self._ws
        if ws is None:
            raise RuntimeError("LcuEventStream no está abierto")
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                event = parse_message(message.data)
                if event is not None and event.uri in self._uris:
                    yield event
            elif message.type == aiohttp.WSMsgType.ERROR:
                raise LcuUnavailable(f"error en el websocket: {describe_error(ws.exception())}")
