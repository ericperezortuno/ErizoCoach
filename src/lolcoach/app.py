"""Montaje de la aplicación: crea los módulos y los conecta por el bus de eventos."""

from __future__ import annotations

import asyncio
import logging
import threading
from enum import StrEnum

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
from .stats.service import StatsService
from .stats.source import StatsBuildSource
from .ui.console import ConsoleReporter
from .ui.labels import GAMEFLOW_LABELS
from .ui.web.demo import publish_demo
from .ui.web.icons import IconProvider
from .ui.web.presenter import UiPresenter
from .ui.web.server import UiServer
from .ui.web.window import AppWindow

log = logging.getLogger(__name__)
ui = logging.getLogger("lolcoach.ui")


class UiMode(StrEnum):
    DESKTOP = "desktop"  # ventana de escritorio propia (pywebview), sin servidor
    BROWSER = "browser"  # respaldo: servidor local + Edge en modo aplicación
    SERVER_ONLY = "server"  # servidor local sin abrir ventana (desarrollo, --demo --no-window)
    NONE = "none"  # solo consola


class Application:
    def __init__(
        self, settings: Settings, *, demo: bool = False, ui_mode: UiMode = UiMode.BROWSER
    ) -> None:
        self.settings = settings
        self.ui_mode = ui_mode
        self.started = threading.Event()  # el bucle ya funciona (para la ventana de escritorio)
        self.loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[None] | None = None
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
        # Fase 5: builds con estadísticas reales (si se han descargado), reglas si no.
        # Sin posición asignada (práctica, a ciegas) se usa la más jugada en alto elo.
        self.build_source = StatsBuildSource(min_games=settings.stats_min_games)
        hint = self.build_source.best_position
        self.loadout = LoadoutService(
            self.bus, self.connector, self.gamedata, settings, position_hint=hint
        )
        self.stats = StatsService(
            self.connector, self.gamedata, self.build_source, settings.stats_dir
        )
        self.builds = BuildService(
            self.bus,
            self.connector,
            self.gamedata,
            settings,
            source=self.build_source,
            position_hint=hint,
        )
        self.live = LiveGameMonitor(
            self.bus,
            self.connector,
            self.gamedata,
            ssl_context,
            lambda: self.builds.current_plan,
            poll_interval=settings.live_poll_interval,
            source=self.build_source,
            position_hint=hint,
        )
        # Ventana para el segundo monitor
        self.presenter = UiPresenter(self.bus, self.gamedata)
        self.icons = IconProvider(self.gamedata, self.connector)
        self.ui_server: UiServer | None = None
        self.window: AppWindow | None = None
        if ui_mode in (UiMode.BROWSER, UiMode.SERVER_ONLY):
            self.ui_server = UiServer(self.presenter, self.gamedata, self.connector)
        if ui_mode == UiMode.BROWSER:
            self.window = AppWindow(
                settings.window_profile_dir, prefer_secondary=settings.window_on_secondary
            )
        if demo:
            self.connector.on_connect(self._publish_demo)

    def request_stop(self) -> None:
        """Para la app desde otro hilo (p. ej., al cerrar la ventana de escritorio)."""
        loop, task = self.loop, self._main_task
        if loop is not None and task is not None and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)

    async def _publish_demo(self, client: LcuClient, _info: object) -> None:
        await publish_demo(self.bus, self.gamedata, client, self.build_source)
        await self.presenter.show_label("Partida de demostración")
        ui.info("Modo demostración: la ventana muestra una partida de ejemplo.")

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._main_task = asyncio.current_task()
        self.started.set()
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
