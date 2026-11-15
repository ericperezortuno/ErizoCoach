"""Abre la ventana de Erizo Coach en tu segundo monitor.

Usa Microsoft Edge (incluido en Windows) en modo aplicación: una ventana sin
barras de navegador. Tiene su propio perfil (dentro de la carpeta de datos de
Erizo Coach), así que no se mezcla con tu navegación ni con tus pestañas.
Si no encuentra Edge, abre la página en tu navegador predeterminado.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
DEFAULT_SIZE = (1056, 700)  # tamaño con el que se abre (el que eligió el usuario)


@dataclass(frozen=True, slots=True)
class Monitor:
    x: int
    y: int
    width: int
    height: int  # área útil (sin la barra de tareas)
    primary: bool


def list_monitors() -> list[Monitor]:
    """Monitores conectados (solo Windows; en otros sistemas, lista vacía)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    monitors: list[Monitor] = []
    user32 = ctypes.windll.user32

    def callback(handle: int, _hdc: int, _rect: object, _data: int) -> int:
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            work = info.rcWork
            monitors.append(
                Monitor(work.left, work.top, work.right - work.left, work.bottom - work.top,
                        bool(info.dwFlags & 1))
            )  # fmt: skip
        return 1

    proc = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )  # fmt: skip
    user32.EnumDisplayMonitors(None, None, proc(callback), 0)
    return monitors


def window_geometry(
    monitors: list[Monitor],
    *,
    prefer_secondary: bool = True,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> tuple[int, int, int, int] | None:
    """Posición y tamaño: centrada en el segundo monitor (o en el principal si no hay)."""
    if not monitors:
        return None
    primary = next((m for m in monitors if m.primary), monitors[0])
    secondary = next((m for m in monitors if not m.primary), None)
    target = secondary if prefer_secondary and secondary is not None else primary
    width, height = min(size[0], target.width), min(size[1], target.height)
    x = target.x + (target.width - width) // 2
    y = target.y + (target.height - height) // 2
    return x, y, width, height


def find_edge() -> Path | None:
    for candidate in EDGE_PATHS:
        if os.path.exists(candidate):
            return Path(candidate)
    return None


class AppWindow:
    def __init__(self, profile_dir: Path, *, prefer_secondary: bool = True) -> None:
        self._profile_dir = profile_dir
        self._prefer_secondary = prefer_secondary
        self._process: subprocess.Popen[bytes] | None = None

    def open(self, url: str) -> None:
        self.close_leftovers()
        edge = find_edge()
        if edge is None:
            log.info("No se encuentra Microsoft Edge: la ventana se abre en tu navegador.")
            webbrowser.open(url)
            return
        args = [
            str(edge),
            f"--app={url}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
        ]
        geometry = window_geometry(list_monitors(), prefer_secondary=self._prefer_secondary)
        if geometry is not None:
            x, y, width, height = geometry
            args += [f"--window-position={x},{y}", f"--window-size={width},{height}"]
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True
        )

    def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()

    def close_leftovers(self) -> int:
        """Cierra ventanas de Erizo Coach que quedaran abiertas (p. ej., si la app se cerró).

        Solo afecta a procesos de Edge que usan el perfil propio de Erizo Coach: nunca a tu
        navegador normal. Devuelve cuántos procesos se han cerrado.
        """
        marker = f"--user-data-dir={self._profile_dir}".lower()
        closed = 0
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() != "msedge.exe":
                continue
            try:
                if any(arg.lower() == marker for arg in proc.cmdline()):
                    proc.terminate()
                    closed += 1
            except (psutil.Error, OSError):
                continue
        if closed:
            log.debug("Cerradas %d ventanas antiguas de Erizo Coach", closed)
        return closed
