"""Ciclo de vida de la conexión con el cliente.

Espera a que se abra el cliente, se conecta de forma segura, escucha sus
eventos y, si se cierra o se reinicia, vuelve a esperar. Nunca deja caer la
aplicación por un fallo de conexión.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Protocol

import psutil

from ..bus import EventBus
from ..events import ClientConnected, ClientDisconnected
from ..logs import REDACTOR
from ..models import ClientInfo
from ..util import as_dict, as_str
from . import endpoints
from .client import LcuClient, LcuError, LcuHttpError, LcuTlsError, LcuUnavailable
from .discovery import LcuCredentials
from .websocket import LcuEvent, LcuEventStream

log = logging.getLogger(__name__)

EventHandler = Callable[[LcuEvent], Awaitable[None]]
ConnectHook = Callable[[LcuClient, ClientInfo], Awaitable[None]]


class Locator(Protocol):
    def locate(self) -> LcuCredentials | None: ...


async def fetch_client_info(client: LcuClient) -> ClientInfo:
    builds, region = await asyncio.gather(
        client.get(endpoints.SYSTEM_BUILDS),
        client.get(endpoints.REGION_LOCALE),
        return_exceptions=True,
    )
    builds = as_dict(None if isinstance(builds, BaseException) else builds)
    region = as_dict(None if isinstance(region, BaseException) else region)
    return ClientInfo(
        version=as_str(builds.get("version"), "desconocida"),
        region=as_str(region.get("region"), "?"),
        locale=as_str(region.get("locale"), "?"),
    )


class LcuConnector:
    def __init__(
        self,
        bus: EventBus,
        locator: Locator,
        ssl_context: ssl.SSLContext,
        *,
        discovery_interval: float = 3.0,
        ready_timeout: float = 120.0,
        ready_poll_interval: float = 1.0,
    ) -> None:
        self._bus = bus
        self._locator = locator
        self._ssl_context = ssl_context
        self._discovery_interval = discovery_interval
        self._ready_timeout = ready_timeout
        self._ready_poll_interval = ready_poll_interval
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._connect_hooks: list[ConnectHook] = []
        self._client: LcuClient | None = None
        self._tls_error_port: int | None = None

    # --- Registro (antes de llamar a run) ---------------------------------

    def on_event(self, uri: str, handler: EventHandler) -> None:
        """Llama a ``handler`` con cada evento del cliente para ``uri``."""
        self._handlers[uri].append(handler)

    def on_connect(self, hook: ConnectHook) -> None:
        """Llama a ``hook`` al conectar, antes de procesar eventos (sincronización inicial)."""
        self._connect_hooks.append(hook)

    # --- Estado -----------------------------------------------------------

    @property
    def client(self) -> LcuClient | None:
        """Cliente conectado (para leer o, en fases futuras, aplicar cambios)."""
        return self._client

    @property
    def connected(self) -> bool:
        return self._client is not None

    # --- Bucle principal --------------------------------------------------

    async def run(self) -> None:
        """Se ejecuta hasta que se cancela (Ctrl+C)."""
        waiting_announced = False
        while True:
            credentials = await asyncio.to_thread(self._locator.locate)
            if credentials is None:
                if not waiting_announced:
                    log.info("Esperando a que abras el cliente de LoL...")
                    waiting_announced = True
                await asyncio.sleep(self._discovery_interval)
                continue
            waiting_announced = False
            await self._run_session(credentials)
            await asyncio.sleep(self._discovery_interval)

    async def _run_session(self, credentials: LcuCredentials) -> None:
        REDACTOR.add_secret(*credentials.secrets)
        connected = False
        try:
            async with LcuClient(credentials, self._ssl_context) as client:
                info = await self._wait_until_ready(client, credentials)
                if info is None:
                    return
                async with LcuEventStream(client, self._handlers.keys()) as stream:
                    self._client = client
                    connected = True
                    log.debug("Conectado al cliente en el puerto %s", credentials.port)
                    await self._bus.publish(ClientConnected(info))
                    for hook in self._connect_hooks:
                        await self._guard(hook(client, info), "sincronización inicial")
                    async for event in stream:
                        for handler in self._handlers.get(event.uri, ()):
                            await self._guard(handler(event), event.uri)
                log.debug("El cliente ha cerrado la conexión")
        except LcuTlsError as exc:
            if self._tls_error_port != credentials.port:  # avisar una vez, no cada 3 s
                self._tls_error_port = credentials.port
                log.error(
                    "Conexión rechazada por seguridad: el certificado no es del cliente "
                    "oficial de Riot (%s)",
                    exc,
                )
            else:
                log.debug("Certificado rechazado de nuevo: %s", exc)
        except LcuUnavailable as exc:
            log.debug("Conexión con el cliente perdida: %s", exc)
        except Exception:
            log.exception("Error inesperado en la conexión con el cliente")
        finally:
            self._client = None
            if connected:
                await self._bus.publish(ClientDisconnected())

    async def _wait_until_ready(
        self, client: LcuClient, credentials: LcuCredentials
    ) -> ClientInfo | None:
        """Al abrirse, el cliente tarda unos segundos en responder: esperamos sin saturarlo."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._ready_timeout
        while True:
            try:
                await client.get(endpoints.GAMEFLOW_PHASE)
                return await fetch_client_info(client)
            except LcuTlsError:
                raise
            except LcuError as exc:
                log.debug("El cliente aún no está listo: %s", exc)
                if isinstance(exc, LcuHttpError) and exc.status in (401, 403):
                    log.debug("Credenciales rechazadas; se volverán a leer")
                    return None
            if credentials.pid is not None and not psutil.pid_exists(credentials.pid):
                return None
            if loop.time() >= deadline:
                log.warning("El cliente de LoL no responde. Se volverá a intentar.")
                return None
            await asyncio.sleep(self._ready_poll_interval)

    async def _guard(self, awaitable: Awaitable[None], context: str) -> None:
        """Un fallo procesando un evento no debe cortar la conexión."""
        try:
            await awaitable
        except LcuUnavailable as exc:
            log.debug("Cliente no disponible durante %s: %s", context, exc)
        except Exception:
            log.exception("Error procesando %s", context)
