"""Montaje de la aplicación: crea los módulos y los conecta por el bus de eventos."""

from __future__ import annotations

import asyncio
import logging

from .builds.service import BuildService
from .bus import EventBus
from .champselect.tracker import ChampSelectTracker
from .config import Settings
from .gamedata.catalog import GameData
from .ingame.monitor import LiveGameMonitor
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
from .ui.web.demo import publish_demo
from .ui.web.presenter import UiPresenter
from .ui.web.server import UiServer
from .ui.web.window import AppWindow

log = logging.getLogger(__name__)
ui = logging.getLogger("lolcoach.ui")


class Application:
    def __init__(self, settings: Settings, *, demo: bool = False) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.gamedata = GameData()
        self.tracker = ChampSelectTracker(self.bus)
        ssl_context = create_ssl_context()
        self.connector = LcuConnector(
            self.bus,
            ClientLocator(settings.league_dir),
            ssl_context,
            discovery_interval=settings.discovery_interval,
        )
        self.monitor = ClientMonitor(self.bus, self.connector, self.gamedata, self.tracker)
        # La consola se suscribe antes que los módulos para mostrar cada paso en orden.
        self.console = ConsoleReporter(self.bus, self.gamedata)
        # Fase 2: runas y hechizos. Fase 3: build en la tienda. Fase 4: partida en vivo.
        self.loadout = LoadoutService(self.bus, self.connector, self.gamedata, settings)
        self.builds = BuildService(self.bus, self.connector, self.gamedata, settings)
        self.live = LiveGameMonitor(
            self.bus,
            self.connector,
            self.gamedata,
            ssl_context,
            lambda: self.builds.current_plan,
            poll_interval=settings.live_poll_interval,
        )
        # Ventana para el segundo monitor
        self.presenter = UiPresenter(self.bus, self.gamedata)
        self.ui_server: UiServer | None = None
        self.window: AppWindow | None = None
        if settings.window or demo:
            self.ui_server = UiServer(self.presenter, self.gamedata, self.connector)
        if settings.window:
            self.window = AppWindow(
                settings.window_profile_dir, prefer_secondary=settings.window_on_secondary
            )
        if demo:
            self.connector.on_connect(self._publish_demo)

    async def _publish_demo(self, client: LcuClient, _info: object) -> None:
        await publish_demo(self.bus, self.gamedata, client)
        await self.presenter.show_label("Partida de demostración")
        ui.info("Modo demostración: la ventana muestra una partida de ejemplo.")

    async def run(self) -> None:
        if self.ui_server is not None:
            await self.ui_server.start()
            if self.window is not None:
                self.window.open(self.ui_server.url)
                ui.info("Ventana abierta (si la cierras, ábrela con: %s)", self.ui_server.url)
            else:
                ui.info("Ventana disponible en: %s", self.ui_server.url)
        try:
            await self.connector.run()
        finally:
            if self.window is not None:
                self.window.close()
            if self.ui_server is not None:
                await self.ui_server.stop()


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
