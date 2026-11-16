"""Intro de unos 5 segundos en tu monitor principal al pulsar «Abrir League of Legends».

* Es una ventana propia (WebView2, como la principal) sin bordes, a pantalla completa en
  el monitor principal y por encima de todo, con fundido de entrada y de salida.
* No te quita nada: los clics la atraviesan (si el cliente de Riot aparece antes de que
  acabe, puedes usarlo) y el teclado sigue en la ventana donde estaba.
* No sale en la barra de tareas ni en Alt+Tab, y se cierra sola.
* Misma seguridad que la ventana principal: página incrustada con CSP estricta, sin red y
  sin puente con la app (la página no puede pedir nada).
* Si algo falla (WebView2, otro sistema…), solo se registra: el botón sigue funcionando.
* Se puede quitar con ``launch_intro = false`` en ``config.toml``.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .page import inline_page

log = logging.getLogger(__name__)

DURATION = 5.0  # segundos, de principio a fin
FADE_IN = 0.25  # la ventana entera aparece...
FADE_OUT = 0.7  # ...y al final se desvanece dejando ver lo que hay detrás
LOAD_TIMEOUT = 8.0  # la página es local: si tarda más, algo va mal y no se enseña
FRAME = 1 / 60
BACKGROUND = "#02050c"  # el mismo negro azulado que el fondo de intro.css (sin destellos)
PLAY_SCRIPT = "window.erizoIntro && window.erizoIntro.play()"


def build_intro_html(nonce: str | None = None) -> str:
    return inline_page(
        "intro.html", css="intro.css", script="intro.js", images=("logo.png",), nonce=nonce
    )


class Screen(Protocol):
    """Lo que la intro necesita de Windows (en las pruebas se sustituye por uno falso)."""

    def primary_rect(self) -> tuple[int, int, int, int] | None: ...
    def find_window(self, title: str) -> int: ...
    def scale(self, hwnd: int) -> float: ...
    def make_overlay(self, hwnd: int) -> None: ...
    def set_alpha(self, hwnd: int, alpha: int) -> None: ...
    def foreground(self) -> int: ...
    def set_foreground(self, hwnd: int) -> None: ...


class LaunchIntro:
    def __init__(
        self,
        *,
        create_window: Callable[..., Any] | None = None,
        screen: Screen | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._create_window = create_window
        self._screen = screen
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing

    def play(self) -> bool:
        """La lanza en segundo plano. False si ya hay una en marcha (o no es Windows)."""
        if self._screen is None and sys.platform != "win32":
            return False
        with self._lock:
            if self._playing:
                return False
            self._playing = True
        threading.Thread(target=self._run, name="erizocoach-intro", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self._show()
        except Exception:
            log.warning("No se ha podido mostrar la intro", exc_info=True)
        finally:
            self._playing = False

    def _show(self) -> None:
        screen = self._screen or WindowsScreen()
        rect = screen.primary_rect()
        if rect is None:
            return
        create = self._create_window
        if create is None:
            import webview

            create = webview.create_window
        title = f"Erizo Coach intro {secrets.token_hex(6)}"  # para encontrar su ventana
        window = create(
            title,
            html=build_intro_html(),
            frameless=True,
            hidden=True,  # se enseña ya colocada e invisible, y luego aparece con fundido
            on_top=True,
            focus=False,
            shadow=False,
            text_select=False,
            background_color=BACKGROUND,
            width=640,
            height=360,
        )
        try:
            if not window.events.loaded.wait(LOAD_TIMEOUT):
                log.warning("La intro no ha cargado a tiempo: no se muestra.")
                return
            hwnd = screen.find_window(title)
            if not hwnd:
                log.warning("No se encuentra la ventana de la intro: no se muestra.")
                return
            screen.make_overlay(hwnd)  # transparente a los clics, fuera de la barra, alfa 0
            x, y, width, height = rect
            scale = screen.scale(hwnd) or 1.0  # pywebview mueve en píxeles lógicos
            window.move(round(x / scale), round(y / scale))
            window.resize(round(width / scale), round(height / scale))
            previous = screen.foreground()
            window.show()  # así WebView2 sabe que es visible y pinta (con alfa 0 no se ve)
            if previous:
                screen.set_foreground(previous)  # el teclado sigue donde estaba
            window.evaluate_js(PLAY_SCRIPT)
            started = self._clock()
            self._fade(screen, hwnd, 0, 255, FADE_IN)
            self._wait_until(started + DURATION - FADE_OUT)
            self._fade(screen, hwnd, 255, 0, FADE_OUT)
        finally:
            with contextlib.suppress(Exception):
                window.destroy()

    def _fade(self, screen: Screen, hwnd: int, start: int, end: int, seconds: float) -> None:
        steps = max(1, round(seconds / FRAME))
        for step in range(1, steps + 1):
            t = step / steps
            eased = t * t * (3 - 2 * t)
            screen.set_alpha(hwnd, round(start + (end - start) * eased))
            self._sleep(seconds / steps)

    def _wait_until(self, moment: float) -> None:
        left = moment - self._clock()
        if left > 0:
            self._sleep(left)


class WindowsScreen:
    """Llamadas a Windows para colocar la intro (todas seguras desde otro hilo)."""

    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x20  # los clics la atraviesan (con WS_EX_LAYERED)
    WS_EX_TOOLWINDOW = 0x80  # ni barra de tareas ni Alt+Tab
    WS_EX_LAYERED = 0x80000  # transparencia de la ventana entera (los fundidos)
    WS_EX_NOACTIVATE = 0x8000000  # no se pone delante para recibir el teclado
    LWA_ALPHA = 0x2
    MONITOR_DEFAULTTOPRIMARY = 1

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        user32 = ctypes.WinDLL("user32")  # copia propia: no cambia las firmas de ctypes.windll
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
        user32.SetLayeredWindowAttributes.argtypes = (
            wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD,
        )  # fmt: skip
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.GetDpiForWindow.restype = wintypes.UINT
        user32.GetDpiForWindow.argtypes = (wintypes.HWND,)
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        self._user32 = user32

    def primary_rect(self) -> tuple[int, int, int, int] | None:
        """El monitor principal entero (también por debajo de la barra de tareas)."""
        ctypes, wintypes = self._ctypes, self._wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [  # noqa: RUF012 - ctypes lo pide así
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitor = self._user32.MonitorFromPoint(wintypes.POINT(0, 0), self.MONITOR_DEFAULTTOPRIMARY)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not monitor or not self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        r = info.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def find_window(self, title: str) -> int:
        for _ in range(40):  # la ventana ya existe al cargar; esto es por si acaso
            hwnd = self._user32.FindWindowW(None, title)
            if hwnd:
                return int(hwnd)
            time.sleep(0.05)
        return 0

    def scale(self, hwnd: int) -> float:
        return (self._user32.GetDpiForWindow(hwnd) or 96) / 96

    def make_overlay(self, hwnd: int) -> None:
        style = self._user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
        style |= (
            self.WS_EX_TRANSPARENT | self.WS_EX_TOOLWINDOW | self.WS_EX_LAYERED
            | self.WS_EX_NOACTIVATE
        )  # fmt: skip
        self._user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, style)
        self.set_alpha(hwnd, 0)

    def set_alpha(self, hwnd: int, alpha: int) -> None:
        self._user32.SetLayeredWindowAttributes(hwnd, 0, max(0, min(255, alpha)), self.LWA_ALPHA)

    def foreground(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def set_foreground(self, hwnd: int) -> None:
        self._user32.SetForegroundWindow(hwnd)
