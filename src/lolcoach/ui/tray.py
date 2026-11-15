"""Icono de Erizo Coach en la bandeja de Windows (junto al reloj).

Sirve para el **segundo plano**: la ventana se esconde (tampoco sale en la barra de
tareas), pero la app sigue funcionando igual (runas, hechizos, build, partida en vivo).

* Clic en el icono: vuelve a abrir la ventana.
* Clic derecho: menú con «Abrir Erizo Coach» y «Cerrar Erizo Coach».
* Si abres Erizo Coach otra vez mientras está en segundo plano, se abre la ventana que ya
  estaba en marcha en lugar de avisar de que ya está abierto (``signal_running``).

Usa la API de Windows directamente (``ctypes``), sin librerías extra, en un hilo propio
con su bucle de mensajes. Si Windows reinicia el Explorador, el icono vuelve a aparecer.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

WINDOW_CLASS = "LoLCoachTrayWindow"
TOOLTIP = "Erizo Coach (en marcha)"

# Mensajes propios (WM_APP = 0x8000)
WM_TRAY = 0x8001  # eventos del icono
WM_SHOW_REQUEST = 0x8002  # otra copia de Erizo Coach pide abrir la ventana
WM_NOTIFY_REQUEST = 0x8003  # mostrar el aviso pendiente (desde otro hilo)

MENU_OPEN = 1
MENU_QUIT = 2


def signal_running(window_class: str = WINDOW_CLASS) -> bool:
    """Pide a la copia de Erizo Coach que ya está abierta que muestre su ventana.

    Devuelve True si había una copia con icono en la bandeja y se le ha avisado.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.WinDLL("user32")  # copia propia: no cambia las firmas de ctypes.windll
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.PostMessageW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    )
    user32.AllowSetForegroundWindow.argtypes = (ctypes.c_uint,)
    hwnd = user32.FindWindowW(window_class, None)
    if not hwnd:
        return False
    # La otra copia es del mismo usuario: permitimos que se ponga delante.
    with contextlib.suppress(OSError, AttributeError):
        user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
    return bool(user32.PostMessageW(hwnd, WM_SHOW_REQUEST, 0, 0))


def default_icon_path() -> Path | None:
    """El icono de la app: el del .exe instalado o, en desarrollo, ``assets/erizocoach.ico``."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)  # el icono va dentro del .exe
    candidate = Path(__file__).resolve().parents[3] / "assets" / "erizocoach.ico"
    return candidate if candidate.exists() else None


class TrayIcon:
    """Icono en la bandeja. ``on_open`` y ``on_quit`` se llaman desde el hilo del icono."""

    def __init__(
        self,
        *,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        icon_path: Path | None = None,
        tooltip: str = TOOLTIP,
        window_class: str = WINDOW_CLASS,  # otro nombre solo en las pruebas
    ) -> None:
        self._window_class = window_class
        self._on_open = on_open
        self._on_quit = on_quit
        self._icon_path = icon_path
        self._tooltip = tooltip
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._win: _Win32Tray | None = None

    def start(self, timeout: float = 5.0) -> bool:
        """Pone el icono en la bandeja. Devuelve False si no se ha podido (no es grave)."""
        if sys.platform != "win32":
            return False
        if self._thread is not None:
            return self._win is not None
        self._thread = threading.Thread(target=self._run, name="lolcoach-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._win is not None

    def notify(self, title: str, text: str) -> None:
        """Aviso de Windows junto al icono (p. ej., la primera vez que pasa a segundo plano)."""
        if self._win is not None:
            self._win.post_notify(title, text)

    def stop(self) -> None:
        win, self._win = self._win, None
        if win is not None:
            win.post_close()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _run(self) -> None:
        try:
            win = _Win32Tray(
                self._window_class, self._tooltip, self._icon_path, self._on_open, self._on_quit
            )
        except Exception as exc:
            log.warning("No se ha podido poner el icono en la bandeja: %s", exc)
            self._ready.set()
            return
        self._win = win
        self._ready.set()
        try:
            win.loop()
        except Exception:
            log.exception("Error en el icono de la bandeja")
        finally:
            win.remove()


class _Win32Tray:
    """La parte de Windows: una ventana oculta que recibe los clics del icono."""

    def __init__(
        self,
        window_class: str,
        tooltip: str,
        icon_path: Path | None,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._class = window_class
        self._on_open = on_open
        self._on_quit = on_quit
        self._tooltip = tooltip[:127]
        self._pending_notify: tuple[str, str] | None = None
        self._lock = threading.Lock()
        self._added = False

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32, self._shell32 = user32, shell32

        LRESULT = ctypes.c_ssize_t
        HANDLE = ctypes.c_void_p
        self._WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, HANDLE, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("style", ctypes.c_uint),
                ("lpfnWndProc", self._WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", HANDLE),
                ("hIcon", HANDLE),
                ("hCursor", HANDLE),
                ("hbrBackground", HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", HANDLE),
            ]

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hWnd", HANDLE),
                ("uID", ctypes.c_uint),
                ("uFlags", ctypes.c_uint),
                ("uCallbackMessage", ctypes.c_uint),
                ("hIcon", HANDLE),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.c_uint),
                ("dwStateMask", ctypes.c_uint),
                ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", ctypes.c_uint),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_uint),
                ("guidItem", GUID),
                ("hBalloonIcon", HANDLE),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", HANDLE),
                ("message", ctypes.c_uint),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", ctypes.c_uint),
                ("pt", POINT),
                ("lPrivate", ctypes.c_uint),
            ]

        self._NOTIFYICONDATAW, self._POINT, self._MSG = NOTIFYICONDATAW, POINT, MSG

        # Firmas (imprescindibles en 64 bits: punteros y LPARAM no caben en un int)
        kernel32.GetModuleHandleW.restype = HANDLE
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = (HANDLE, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
        user32.RegisterClassExW.restype = ctypes.c_ushort
        user32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
        user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, HANDLE)
        user32.CreateWindowExW.restype = HANDLE
        user32.CreateWindowExW.argtypes = (
            ctypes.c_uint, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            HANDLE, HANDLE, HANDLE, ctypes.c_void_p,
        )  # fmt: skip
        user32.DestroyWindow.argtypes = (HANDLE,)
        user32.PostMessageW.argtypes = (HANDLE, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
        user32.GetMessageW.argtypes = (ctypes.POINTER(MSG), HANDLE, ctypes.c_uint, ctypes.c_uint)
        user32.TranslateMessage.argtypes = (ctypes.POINTER(MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(MSG),)
        user32.DispatchMessageW.restype = LRESULT
        user32.RegisterWindowMessageW.restype = ctypes.c_uint
        user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
        user32.LoadImageW.restype = HANDLE
        user32.LoadImageW.argtypes = (
            HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        )
        user32.LoadIconW.restype = HANDLE
        user32.LoadIconW.argtypes = (HANDLE, ctypes.c_void_p)
        user32.DestroyIcon.argtypes = (HANDLE,)
        user32.CreatePopupMenu.restype = HANDLE
        user32.AppendMenuW.argtypes = (HANDLE, ctypes.c_uint, ctypes.c_size_t, wintypes.LPCWSTR)
        user32.SetMenuDefaultItem.argtypes = (HANDLE, ctypes.c_uint, ctypes.c_uint)
        user32.TrackPopupMenu.argtypes = (
            HANDLE,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            HANDLE,
            ctypes.c_void_p,
        )
        user32.DestroyMenu.argtypes = (HANDLE,)
        user32.SetForegroundWindow.argtypes = (HANDLE,)
        user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
        user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        shell32.Shell_NotifyIconW.argtypes = (ctypes.c_uint, ctypes.POINTER(NOTIFYICONDATAW))
        shell32.ExtractIconExW.restype = ctypes.c_uint
        shell32.ExtractIconExW.argtypes = (
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(HANDLE),
            ctypes.c_uint,
        )

        self._hinstance = kernel32.GetModuleHandleW(None)
        # Antes de crear la ventana: Windows le manda mensajes ya durante su creación.
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = self._WNDPROC(self._handle)  # se guarda: si se libera, Windows falla
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinstance
        wc.lpszClassName = window_class
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "RegisterClassExW")
        # Ventana normal pero nunca visible: así recibe el aviso de que el Explorador
        # se ha reiniciado (las ventanas «solo mensajes» no lo reciben).
        self._hwnd = user32.CreateWindowExW(
            0, window_class, "Erizo Coach", 0, 0, 0, 0, 0, None, None, self._hinstance, None
        )
        if not self._hwnd:
            error = ctypes.get_last_error()
            user32.UnregisterClassW(window_class, self._hinstance)
            raise OSError(error, "CreateWindowExW")
        self._icon, self._owns_icon = self._load_icon(icon_path)
        self._add()

    # --- Desde otros hilos ---------------------------------------------------------------

    def post_notify(self, title: str, text: str) -> None:
        with self._lock:
            self._pending_notify = (title[:63], text[:255])
        self._user32.PostMessageW(self._hwnd, WM_NOTIFY_REQUEST, 0, 0)

    def post_close(self) -> None:
        self._user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # --- Hilo del icono ------------------------------------------------------------------

    def loop(self) -> None:
        msg = self._MSG()
        while self._user32.GetMessageW(self._ct.byref(msg), None, 0, 0) > 0:
            self._user32.TranslateMessage(self._ct.byref(msg))
            self._user32.DispatchMessageW(self._ct.byref(msg))

    def remove(self) -> None:
        if self._added:
            self._shell32.Shell_NotifyIconW(2, self._ct.byref(self._data()))  # NIM_DELETE
            self._added = False
        if self._owns_icon and self._icon:
            self._user32.DestroyIcon(self._icon)
            self._icon = None
        self._user32.UnregisterClassW(self._class, self._hinstance)

    def _load_icon(self, path: Path | None) -> tuple[int | None, bool]:
        ct, user32 = self._ct, self._user32
        if path is not None:
            if path.suffix.lower() == ".exe":
                small = ct.c_void_p()
                if self._shell32.ExtractIconExW(str(path), 0, None, ct.byref(small), 1) and small:
                    return small.value, True
            else:
                size = user32.GetSystemMetrics(49)  # SM_CXSMICON
                handle = user32.LoadImageW(None, str(path), 1, size, size, 0x10)  # LR_LOADFROMFILE
                if handle:
                    return handle, True
        return user32.LoadIconW(None, ct.c_void_p(32512)), False  # IDI_APPLICATION

    def _data(self, flags: int = 0) -> object:
        data = self._NOTIFYICONDATAW()
        data.cbSize = self._ct.sizeof(self._NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        return data

    def _add(self) -> None:
        # NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        data = self._data(0x01 | 0x02 | 0x04 | 0x80)
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._tooltip
        if not self._shell32.Shell_NotifyIconW(0, self._ct.byref(data)):  # NIM_ADD
            log.debug("Windows no ha aceptado el icono de la bandeja (¿Explorador cerrado?)")
            return
        data.uVersion = 4  # NOTIFYICON_VERSION_4: clics con coordenadas y teclado
        self._shell32.Shell_NotifyIconW(4, self._ct.byref(data))  # NIM_SETVERSION
        self._added = True

    def _show_notification(self) -> None:
        with self._lock:
            pending, self._pending_notify = self._pending_notify, None
        if pending is None or not self._added:
            return
        data = self._data(0x10)  # NIF_INFO
        data.szInfoTitle, data.szInfo = pending
        data.dwInfoFlags = 0x04 | 0x20  # NIIF_USER (icono de la app) | NIIF_LARGE_ICON
        data.hBalloonIcon = self._icon
        self._shell32.Shell_NotifyIconW(1, self._ct.byref(data))  # NIM_MODIFY

    def _menu(self) -> None:
        user32, ct = self._user32, self._ct
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, 0, MENU_OPEN, "Abrir Erizo Coach")
            user32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
            user32.AppendMenuW(menu, 0, MENU_QUIT, "Cerrar Erizo Coach")
            user32.SetMenuDefaultItem(menu, MENU_OPEN, 0)
            point = self._POINT()
            user32.GetCursorPos(ct.byref(point))
            # Sin esto, el menú no se cierra al hacer clic fuera (comportamiento de Windows).
            user32.SetForegroundWindow(self._hwnd)
            # TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY
            choice = user32.TrackPopupMenu(
                menu, 0x2 | 0x100 | 0x80, point.x, point.y, 0, self._hwnd, None
            )
            user32.PostMessageW(self._hwnd, 0, 0, 0)  # WM_NULL
        finally:
            user32.DestroyMenu(menu)
        if choice == MENU_OPEN:
            self._call(self._on_open)
        elif choice == MENU_QUIT:
            self._call(self._on_quit)

    def _call(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            log.exception("Error al atender el icono de la bandeja")

    def _handle(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (0x0400, 0x0401):  # NIN_SELECT (clic), NIN_KEYSELECT (teclado)
                self._call(self._on_open)
            elif event == 0x007B:  # WM_CONTEXTMENU (clic derecho o tecla de menú)
                self._menu()
            return 0
        if message == WM_SHOW_REQUEST:
            self._call(self._on_open)
            return 0
        if message == WM_NOTIFY_REQUEST:
            self._show_notification()
            return 0
        if message == self._taskbar_created and message:
            self._added = False
            self._add()
            return 0
        if message == 0x0010:  # WM_CLOSE
            self._user32.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:  # WM_DESTROY
            if self._added:
                self._shell32.Shell_NotifyIconW(2, self._ct.byref(self._data()))  # NIM_DELETE
                self._added = False
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)
