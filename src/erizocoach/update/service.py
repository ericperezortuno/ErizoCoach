"""Servicio de actualización: aviso periódico y descarga verificada del instalador.

* Solo en la app instalada (congelada), con ``update_check`` activo y con la clave pública
  de firma puesta (:func:`should_check`).
* A los 30 s de arrancar y después cada 6 h mira la última release: descarga el manifiesto y
  su firma, comprueba la firma y avisa solo si la versión es estrictamente mayor. Los fallos
  de red al comprobar no se enseñan: se anotan en el registro y se vuelve a intentar.
* Al pedirlo el usuario (``UpdateRequested``) descarga el instalador con su hash, lo lanza
  aparte y cierra la app. Solo se actualiza con el cliente de LoL cerrado o en el menú
  (:data:`SAFE_PHASES`); la fase se vuelve a comprobar justo antes de lanzar el instalador.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..bus import EventBus
from ..config import Settings
from ..events import (
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    UpdateRequested,
    UpdateState,
    UpdateStatus,
)
from ..lcu.launcher import CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS
from ..models import GameflowPhase
from .download import MANIFEST_URL, SIGNATURE_URL, Downloader, installer_url
from .manifest import Manifest, UpdateError, parse_version, verify_manifest

log = logging.getLogger(__name__)

# Fases en las que se puede cerrar la app para instalar (con el cliente desconectado también).
SAFE_PHASES = frozenset({GameflowPhase.NONE, GameflowPhase.LOBBY, GameflowPhase.END_OF_GAME})
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 1024
DOWNLOAD_MESSAGE = (
    "No se ha podido descargar la actualización. Comprueba tu conexión y vuelve a intentarlo."
)
UNEXPECTED_MESSAGE = "No se ha podido actualizar. Vuelve a intentarlo."
UNSAFE_MESSAGE = "Ahora no se puede actualizar: espera a estar en el menú o en la sala."
NOT_CLOSED_MESSAGE = (
    "No se ha podido cerrar Erizo Coach para actualizar: ciérralo y vuelve a abrirlo."
)
# Tras lanzar el instalador: lo que se espera a que la app se cierre (el instalador espera 30 s)
QUIT_TIMEOUT = 45.0


def disabled_reason(settings: Settings, public_key: bytes) -> str | None:
    """Por qué no se busca versión nueva (``None`` si se busca)."""
    if not getattr(sys, "frozen", False):
        return "se ejecuta desde el código, no la app instalada"
    if not settings.update_check:
        return "update_check = false en la configuración"
    if not public_key:
        return "falta la clave pública de firma"
    return None


def should_check(settings: Settings, public_key: bytes) -> bool:
    """Solo se busca en la app instalada, con el ajuste activo y la clave de firma puesta."""
    return disabled_reason(settings, public_key) is None


def default_download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "erizocoach-actualizar"


def launch_installer_detached(path: Path) -> None:
    """Lanza el instalador aparte (sobrevive al cierre de la app) y le pasa nuestro PID."""
    subprocess.Popen(  # ruta descargada y verificada; argumentos fijos
        [str(path), "--actualizar", str(os.getpid())],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


class UpdateService:
    def __init__(
        self,
        bus: EventBus,
        *,
        current_version: str,
        public_key: bytes,
        downloader: Downloader,
        download_dir: Path | None = None,
        launch_installer: Callable[[Path], None] = launch_installer_detached,
        quit_app: Callable[[], None],
        first_delay: float = 30.0,
        interval: float = 6 * 3600,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bus = bus
        self._current = current_version
        self._public_key = public_key
        self._downloader = downloader
        self._download_dir = download_dir or default_download_dir()
        self._launch_installer = launch_installer
        self._quit_app = quit_app
        self._first_delay = first_delay
        self._interval = interval
        self._sleep = sleep
        self._connected = False
        self._phase = GameflowPhase.NONE
        self._available: Manifest | None = None
        self._update_task: asyncio.Task[None] | None = None
        self._quit_watch: asyncio.Task[None] | None = None
        self._cleaned = False
        bus.subscribe(ClientConnected, self._on_connected)
        bus.subscribe(ClientDisconnected, self._on_disconnected)
        bus.subscribe(GameflowPhaseChanged, self._on_phase)
        bus.subscribe(UpdateRequested, self._on_requested)

    @property
    def safe(self) -> bool:
        """Se puede cerrar la app: cliente desconectado o en el menú."""
        return not self._connected or self._phase in SAFE_PHASES

    def _on_connected(self, _event: ClientConnected) -> None:
        self._connected = True
        self._phase = GameflowPhase.UNKNOWN  # hasta saber la fase real, no es seguro

    def _on_disconnected(self, _event: ClientDisconnected) -> None:
        self._connected = False
        self._phase = GameflowPhase.NONE

    def _on_phase(self, event: GameflowPhaseChanged) -> None:
        self._phase = event.current

    async def run(self) -> None:
        """Comprueba a los ``first_delay`` s y luego cada ``interval`` s (hasta cancelarla)."""
        try:
            await self._sleep(self._first_delay)
            while True:
                await self.check()
                await self._sleep(self._interval)
        finally:  # la app se cierra: una actualización a medias se cancela (sin archivo suelto)
            await self._cancel_update()

    @property
    def _updating(self) -> bool:
        return any(t is not None and not t.done() for t in (self._update_task, self._quit_watch))

    @property
    def update_task(self) -> asyncio.Task[None] | None:
        """La actualización en curso (o la última), para esperarla en las pruebas."""
        return self._update_task

    @property
    def quit_watch_task(self) -> asyncio.Task[None] | None:
        """La espera a que la app se cierre tras lanzar el instalador (para las pruebas)."""
        return self._quit_watch

    async def _cancel_update(self) -> None:
        for task in (self._update_task, self._quit_watch):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def check(self) -> Manifest | None:
        """Devuelve el manifiesto si hay una versión más nueva (y la anuncia); si no, ``None``."""
        if not self._cleaned:  # el instalador de la actualización anterior ya no hace falta
            self._cleaned = True
            await asyncio.to_thread(shutil.rmtree, self._download_dir, ignore_errors=True)
        try:
            manifest = await self._fetch_manifest()
            if manifest is None:
                return None
            if parse_version(manifest.version) <= parse_version(self._current):
                log.debug(
                    "Última versión publicada %s; la instalada es %s",
                    manifest.version,
                    self._current,
                )
                return None
        except UpdateError as exc:
            log.debug("Sin comprobación de versión: %s", exc)
            return None
        except Exception:  # un fallo aquí nunca debe tumbar la app
            log.warning("Fallo inesperado al buscar versión nueva", exc_info=True)
            return None
        log.info(
            "Nueva versión %s disponible (la instalada es %s)", manifest.version, self._current
        )
        self._available = manifest
        if not self._updating:  # con una descarga en marcha, la ventana sigue con su progreso
            await self._status(manifest, UpdateState.AVAILABLE)
        return manifest

    async def _fetch_manifest(self) -> Manifest | None:
        data = await self._downloader.fetch(MANIFEST_URL, MAX_MANIFEST_BYTES)
        if data is None:
            log.debug("No hay manifiesto de versión publicado (404)")
            return None
        signature = await self._downloader.fetch(SIGNATURE_URL, MAX_SIGNATURE_BYTES)
        if signature is None:
            log.debug("No hay firma del manifiesto publicada (404)")
            return None
        return verify_manifest(data, signature, self._public_key)

    async def _status(
        self, manifest: Manifest, state: UpdateState, progress: int = 0, error: str = ""
    ) -> None:
        await self._bus.publish(
            UpdateStatus(
                version=manifest.version,
                notes=manifest.notes,
                state=state,
                progress=progress,
                error=error,
            )
        )

    def _on_requested(self, _event: UpdateRequested) -> None:
        """Arranca la actualización en segundo plano: quien publica el evento no espera."""
        manifest = self._available
        if manifest is None or self._updating:
            return
        self._update_task = asyncio.create_task(self._update(manifest))
        self._update_task.add_done_callback(self._log_failure)

    @staticmethod
    def _log_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("Fallo inesperado al actualizar", exc_info=task.exception())

    async def _update(self, manifest: Manifest) -> None:
        if not self.safe:
            await self._status(manifest, UpdateState.FAILED, error=UNSAFE_MESSAGE)
            return
        await asyncio.to_thread(shutil.rmtree, self._download_dir, ignore_errors=True)
        path = self._download_dir / manifest.installer
        try:
            await asyncio.to_thread(self._download_dir.mkdir, parents=True, exist_ok=True)
            await self._status(manifest, UpdateState.DOWNLOADING, 0)

            async def progress(percent: int) -> None:  # el Downloader ya avisa en porcentaje
                await self._status(manifest, UpdateState.DOWNLOADING, percent)

            await self._downloader.to_file(
                installer_url(manifest), path, manifest.size, manifest.sha256, progress
            )
        except (UpdateError, OSError) as exc:
            log.warning("Descarga de la actualización fallida: %s", exc)
            await self._fail(manifest, DOWNLOAD_MESSAGE, path)
            return
        except Exception:  # un fallo inesperado no debe dejar la ventana a medias
            log.exception("Fallo inesperado al descargar la actualización")
            await self._fail(manifest, UNEXPECTED_MESSAGE, path)
            return
        if not self.safe:  # la fase pudo cambiar durante la descarga
            await self._fail(manifest, UNSAFE_MESSAGE, path)
            return
        try:
            self._launch_installer(path)
        except OSError as exc:
            log.warning("No se pudo lanzar el instalador: %s", exc)
            await self._fail(
                manifest, "No se ha podido abrir el instalador. Inténtalo de nuevo.", path
            )
            return
        log.info(
            "Actualizando a %s: se abre el instalador y Erizo Coach se cierra", manifest.version
        )
        await self._status(manifest, UpdateState.INSTALLING, 100)
        self._quit_app()
        self._quit_watch = asyncio.create_task(self._fail_if_still_open(manifest, path))
        self._quit_watch.add_done_callback(self._log_failure)

    async def _fail_if_still_open(self, manifest: Manifest, path: Path) -> None:
        """Si la app sigue viva pasado el plazo, el instalador ya se habrá rendido: avisa."""
        await self._sleep(QUIT_TIMEOUT)
        log.warning(
            "Erizo Coach no se ha cerrado tras lanzar el instalador de %s", manifest.version
        )
        await self._fail(manifest, NOT_CLOSED_MESSAGE, path)

    async def _fail(self, manifest: Manifest, error: str, path: Path) -> None:
        with contextlib.suppress(OSError):  # puede seguir en uso por el instalador
            await asyncio.to_thread(path.unlink, missing_ok=True)
        await self._status(manifest, UpdateState.FAILED, error=error)
