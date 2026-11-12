"""Montaje de la aplicación: crea los módulos y los conecta por el bus de eventos."""

from __future__ import annotations

import asyncio
import logging

from .bus import EventBus
from .champselect.tracker import ChampSelectTracker
from .config import Settings
from .gamedata.catalog import GameData
from .lcu import endpoints
from .lcu.client import LcuClient, LcuError, LcuTlsError
from .lcu.connector import LcuConnector, fetch_client_info
from .lcu.discovery import ClientLocator
from .lcu.tls import create_ssl_context
from .lcu.websocket import LcuEventStream
from .loadout.service import LoadoutService
from .logs import REDACTOR
from .models import GameflowPhase
from .monitor import ClientMonitor
from .ui.console import ConsoleReporter
from .ui.labels import GAMEFLOW_LABELS

log = logging.getLogger(__name__)
ui = logging.getLogger("lolcoach.ui")


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.gamedata = GameData()
        self.tracker = ChampSelectTracker(self.bus)
        self.connector = LcuConnector(
            self.bus,
            ClientLocator(settings.league_dir),
            create_ssl_context(),
            discovery_interval=settings.discovery_interval,
        )
        self.monitor = ClientMonitor(self.bus, self.connector, self.gamedata, self.tracker)
        # La consola se suscribe antes que los módulos para mostrar cada paso en orden.
        self.console = ConsoleReporter(self.bus, self.gamedata)
        # Fase 2: runas y hechizos. (Fase 3+: builds, ventana del segundo monitor...)
        self.loadout = LoadoutService(self.bus, self.connector, self.gamedata, settings)

    async def run(self) -> None:
        await self.connector.run()


async def run_check(settings: Settings) -> int:
    """Diagnóstico: detección, conexión segura, eventos y datos. Devuelve 0 si todo va bien."""
    locator = ClientLocator(settings.league_dir)
    credentials = await asyncio.to_thread(locator.locate)
    if credentials is None:
        ui.error("No se encuentra el cliente de LoL. ¿Está abierto?")
        return 1
    REDACTOR.add_secret(*credentials.secrets)
    ui.info("[OK] Cliente detectado (carpeta: %s, puerto %s)", locator.league_dir, credentials.port)

    try:
        async with LcuClient(credentials, create_ssl_context()) as client:
            info = await fetch_client_info(client)
            ui.info("[OK] Conexión cifrada y verificada con el certificado de Riot")
            ui.info(
                "[OK] Cliente: versión %s, servidor %s, idioma %s",
                info.version,
                info.region,
                info.locale,
            )

            phase = GameflowPhase.parse(await client.get(endpoints.GAMEFLOW_PHASE))
            ui.info("[OK] Estado actual: %s", GAMEFLOW_LABELS.get(phase) or phase.value)

            async with LcuEventStream(client, [endpoints.GAMEFLOW_PHASE]):
                ui.info("[OK] Canal de eventos en tiempo real abierto")

            gamedata = GameData()
            await gamedata.load(client, info.version)
            ui.info("[OK] Datos del juego: %d campeones cargados", gamedata.champion_count)
    except LcuTlsError as exc:
        ui.error("El certificado del cliente no es válido: %s", exc)
        return 1
    except LcuError as exc:
        ui.error("No se pudo completar la comprobación: %s", exc)
        return 1
    ui.info("Todo correcto.")
    return 0
