"""Instalador de Erizo Coach en un solo archivo (para pasárselo a otra persona).

Se construye con ``uv run python scripts/build_installer.py`` y lleva dentro la app
(``payload.zip``). Al abrirlo:

* Pregunta si quieres instalar y lo instala **solo para tu usuario**, sin permisos de
  administrador, en ``%LOCALAPPDATA%\\Programs\\Erizo Coach``.
* Crea accesos directos en el menú Inicio y en el escritorio.
* Aparece en *Configuración > Aplicaciones > Aplicaciones instaladas*, desde donde se
  desinstala (se quitan la app y los accesos directos; los datos se conservan).
* Si ya estaba instalado, lo actualiza. Si Erizo Coach está abierto, pide cerrarlo antes.
* Si al PC le falta **WebView2** (el motor de Microsoft con el que se dibuja la ventana; viene
  de serie en Windows 11 y en casi todos los Windows 10), lo descarga de Microsoft, comprueba
  que el archivo está firmado por Microsoft y lo instala para tu usuario. Si no puede (por
  ejemplo, sin internet), Erizo Coach funciona igual: abre su ventana con Edge.

Solo usa la biblioteca estándar de Python (``ctypes`` y ``tkinter`` para las ventanas).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Erizo Coach"
EXE_NAME = "Erizo Coach.exe"
SHORTCUT_NAME = "Erizo Coach.lnk"
UNINSTALL_SCRIPT = "desinstalar.ps1"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ErizoCoach"
# Hasta la 0.18 la app se llamaba «LoL Coach»: al instalar se quita esa instalación antigua.
LEGACY_NAME = "LoL Coach"
LEGACY_EXE = "LoL Coach.exe"
LEGACY_SHORTCUT = "LoL Coach.lnk"
LEGACY_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LoLCoach"
PROCESS_NAMES = ("Erizo Coach", "LoL Coach")
PAYLOAD = "payload.zip"
VERSION_FILE = "version.txt"  # dentro del paquete: la versión que se instala
ICON_FILE = "erizocoach.ico"  # dentro del paquete: icono de la ventanita de progreso

# WebView2 (motor de la ventana): instalador oficial «Evergreen bootstrapper» de Microsoft
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_SETUP = "MicrosoftEdgeWebview2Setup.exe"
WEBVIEW2_MAX_BYTES = 20_000_000  # el bootstrapper ocupa ~2 MB; más es que algo va mal
WEBVIEW2_DOWNLOAD_TIMEOUT = 60  # segundos sin respuesta
WEBVIEW2_INSTALL_TIMEOUT = 15 * 60  # descarga el motor (~150 MB) e instala
MICROSOFT_SIGNER = "O=Microsoft Corporation"

MB_OK, MB_YESNO, MB_ICONINFO, MB_ICONWARN, MB_ICONERROR, IDYES = 0, 4, 0x40, 0x30, 0x10, 6
CREATE_NO_WINDOW = 0x08000000

# Desinstalador: quita accesos directos, la entrada de «Aplicaciones» y la carpeta de la
# app (PowerShell lee el script entero antes de ejecutarlo, así que puede borrarse a sí
# mismo). Los datos de %LOCALAPPDATA%\LoLCoach no se tocan.
UNINSTALL_PS1 = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName PresentationFramework
$box = [System.Windows.MessageBox]
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Get-Process -Name 'Erizo Coach' | Where-Object { $_.Path -like "$app\*" }) {
    $box::Show('Erizo Coach está abierto: ciérralo y vuelve a desinstalarlo.', 'Erizo Coach')
    exit 1
}
$question = '¿Quitar Erizo Coach de este PC? Tus datos (estadísticas y ajustes) se conservan.'
if ($box::Show($question, 'Desinstalar Erizo Coach', 'YesNo') -ne 'Yes') { exit 0 }
foreach ($folder in 'Programs', 'Desktop') {
    $link = Join-Path ([Environment]::GetFolderPath($folder)) 'Erizo Coach.lnk'
    Remove-Item -LiteralPath $link -Force
}
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ErizoCoach'
Remove-Item -LiteralPath $key -Recurse -Force
Set-Location -LiteralPath $env:TEMP
Remove-Item -LiteralPath $app -Recurse -Force
$box::Show('Erizo Coach se ha desinstalado.', 'Erizo Coach') | Out-Null
"""


class InstallError(Exception):
    """No se puede instalar (mensaje para la persona que instala)."""


@dataclass(frozen=True, slots=True)
class Places:
    target: Path  # carpeta de la app
    shortcuts: tuple[Path, ...]  # carpetas donde van los accesos directos


def default_places() -> Places:
    local = Path(os.environ["LOCALAPPDATA"])
    return Places(
        target=local / "Programs" / APP_NAME,
        shortcuts=(known_folder("Programs"), known_folder("Desktop")),
    )


def known_folder(name: str) -> Path:
    """Carpeta de Windows de verdad (el escritorio puede estar en OneDrive)."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
        capture_output=True, text=True, check=True, creationflags=CREATE_NO_WINDOW,
    )  # fmt: skip
    return Path(result.stdout.strip())


def app_running(*targets: Path) -> bool:
    """¿Está abierta la app instalada en alguna de ``targets``? (sin librerías extra)."""
    names = ",".join(f"'{name}'" for name in PROCESS_NAMES)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process -Name {names} -ErrorAction SilentlyContinue | "
         "ForEach-Object { $_.Path }"],
        capture_output=True, text=True, check=False, creationflags=CREATE_NO_WINDOW,
    )  # fmt: skip
    prefixes = tuple(str(target).lower() + os.sep for target in targets)
    return any(line.strip().lower().startswith(prefixes) for line in result.stdout.splitlines())


def extract(payload: Path, target: Path) -> None:
    """Copia la app a ``target`` (sustituye una instalación anterior)."""
    with zipfile.ZipFile(payload) as archive:
        names = archive.namelist()
        if EXE_NAME not in names:
            raise InstallError("El instalador está incompleto (falta la app). Vuelve a pedirlo.")
        for name in names:  # nada puede salirse de la carpeta de la app
            path = (target / name).resolve()
            if not path.is_relative_to(target.resolve()):
                raise InstallError("El instalador está dañado. Vuelve a pedirlo.")
        staging = target.with_name(target.name + ".nuevo")
        shutil.rmtree(staging, ignore_errors=True)
        archive.extractall(staging)
    (staging / UNINSTALL_SCRIPT).write_text(UNINSTALL_PS1, encoding="utf-8-sig")
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)  # la versión anterior solo se quita cuando la nueva está lista


def create_shortcut(link: Path, exe: Path) -> None:
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LC_LINK); "
        "$s.TargetPath = $env:LC_TARGET; $s.WorkingDirectory = $env:LC_DIR; "
        "$s.IconLocation = $env:LC_TARGET + ',0'; "
        "$s.Description = 'Erizo Coach: runas, hechizos y builds para League of Legends'; "
        "$s.Save()"
    )
    env = {**os.environ, "LC_LINK": str(link), "LC_TARGET": str(exe), "LC_DIR": str(exe.parent)}
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        env=env, check=True, creationflags=CREATE_NO_WINDOW,
    )  # fmt: skip


def register_uninstaller(target: Path, version: str) -> None:
    """Entrada en «Aplicaciones instaladas» (solo para tu usuario)."""
    import winreg

    script = target / UNINSTALL_SCRIPT
    size_kb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": version,
        "Publisher": APP_NAME,
        "DisplayIcon": str(target / EXE_NAME),
        "InstallLocation": str(target),
        "UninstallString": (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
            f'-File "{script}"'
        ),
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def remove_legacy(places: Places, *, register: bool = True) -> None:
    """Quita la instalación con el nombre antiguo («LoL Coach»): carpeta, accesos y entrada.

    Solo borra la carpeta si de verdad es la app antigua (tiene su ejecutable). Los datos
    (estadísticas, ajustes, clave) no se tocan: la app nueva sigue usando los mismos.
    """
    for folder in places.shortcuts:
        with contextlib.suppress(OSError):
            (folder / LEGACY_SHORTCUT).unlink(missing_ok=True)
    legacy = places.target.with_name(LEGACY_NAME)
    if legacy != places.target and (legacy / LEGACY_EXE).is_file():
        shutil.rmtree(legacy, ignore_errors=True)
    if register:
        import winreg

        with contextlib.suppress(OSError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, LEGACY_UNINSTALL_KEY)


def install(payload: Path, places: Places, version: str, *, register: bool = True) -> Path:
    """Instala o actualiza. Devuelve la ruta del ejecutable."""
    if app_running(places.target, places.target.with_name(LEGACY_NAME)):
        raise InstallError("Erizo Coach está abierto: ciérralo y vuelve a abrir el instalador.")
    extract(payload, places.target)
    exe = places.target / EXE_NAME
    for folder in places.shortcuts:
        folder.mkdir(parents=True, exist_ok=True)
        create_shortcut(folder / SHORTCUT_NAME, exe)
    if register:
        register_uninstaller(places.target, version)
    remove_legacy(places, register=register)
    return exe


# --- WebView2 ----------------------------------------------------------------------------


def webview2_installed() -> bool:
    """¿Tiene el PC el motor WebView2? (Las mismas claves que mira la app y Microsoft)."""
    import winreg

    clients = rf"Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"
    locations = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{clients}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{clients}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\{clients}"),
    )
    for root, path in locations:
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return True
    return False


def download(url: str, dest: Path, *, max_bytes: int = WEBVIEW2_MAX_BYTES) -> None:
    """Descarga por HTTPS (con el certificado comprobado) y con un tamaño máximo."""
    if not url.startswith("https://"):
        raise InstallError("solo se descarga por HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "ErizoCoach-Installer"})
    with urllib.request.urlopen(request, timeout=WEBVIEW2_DOWNLOAD_TIMEOUT) as response:
        if not response.geturl().startswith("https://"):
            raise InstallError("la descarga ha salido de HTTPS")
        size = 0
        with dest.open("wb") as out:
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise InstallError("la descarga es más grande de lo esperado")
                out.write(chunk)


def signed_by_microsoft(path: Path) -> bool:
    """¿Lleva el archivo una firma digital válida de Microsoft Corporation?"""
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:LC_FILE; "
        "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        env={**os.environ, "LC_FILE": str(path)},
        capture_output=True, text=True, check=False, creationflags=CREATE_NO_WINDOW,
    )  # fmt: skip
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0] != "Valid":
        return False
    return MICROSOFT_SIGNER in [part.strip() for part in lines[1].split(",")]


def install_webview2(work: Path) -> None:
    """Descarga el instalador oficial de WebView2, comprueba su firma y lo ejecuta.

    Sin permisos de administrador, Microsoft lo instala solo para tu usuario.
    """
    setup = work / WEBVIEW2_SETUP
    try:
        download(WEBVIEW2_URL, setup)
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(f"no se ha podido descargar: {exc}") from exc
    if not signed_by_microsoft(setup):
        raise InstallError("el archivo descargado no tiene la firma de Microsoft")
    try:
        result = subprocess.run(
            [str(setup), "/silent", "/install"],
            check=False, timeout=WEBVIEW2_INSTALL_TIMEOUT, creationflags=CREATE_NO_WINDOW,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise InstallError("la instalación ha tardado demasiado") from exc
    if not webview2_installed():
        code = result.returncode
        raise InstallError(f"el instalador de Microsoft ha terminado con el código {code}")


# --- Ventanas -----------------------------------------------------------------------------


def message(text: str, flags: int = MB_OK | MB_ICONINFO) -> int:
    import ctypes

    return int(ctypes.windll.user32.MessageBoxW(None, text, f"Instalar {APP_NAME}", flags))


def with_progress[T](text: str, work: Callable[[Callable[[str], None]], T]) -> T:
    """Ejecuta ``work`` mostrando una ventanita de «trabajando…» que no se puede cerrar.

    ``work`` recibe una función para cambiar el texto. Sin ``tkinter``, va sin ventana.
    """
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
    except Exception:  # sin tkinter o sin Tcl: se instala igual, solo que sin ventanita
        return work(lambda _text: None)

    outcome: dict[str, object] = {}

    def say(new_text: str) -> None:
        root.after(0, lambda: label.config(text=new_text))

    say.window = True  # type: ignore[attr-defined]

    def run() -> None:
        try:
            outcome["value"] = work(say)
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            root.after(0, root.destroy)

    root.title(f"Instalar {APP_NAME}")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)  # que no se cierre a medias
    icon = bundled(ICON_FILE)
    if icon.exists():
        with contextlib.suppress(Exception):
            root.iconbitmap(default=str(icon))
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, text=text, width=58, wraplength=420, justify="left")
    label.pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate", length=420)
    bar.pack(pady=(14, 0))
    bar.start(12)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.attributes("-topmost", True)
    threading.Thread(target=run, name="instalar", daemon=True).start()
    root.mainloop()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]


def bundled(name: str) -> Path:
    """Archivo incluido en el instalador (PyInstaller lo descomprime en ``_MEIPASS``)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    version = bundled(VERSION_FILE).read_text(encoding="utf-8").strip()
    if len(args) == 2 and args[0] == "--destino":
        # Prueba sin preguntas: instala en una carpeta, con los accesos directos dentro y sin
        # tocar el registro ni WebView2 (para comprobar el instalador sin cambiar tu
        # instalación). Deja en la carpeta si se vio la ventanita de progreso.
        target = Path(args[1]).resolve() / APP_NAME
        places = Places(target=target, shortcuts=(target.parent / "accesos",))
        shown: list[bool] = []

        def work(say: Callable[[str], None]) -> None:
            shown.append(getattr(say, "window", False))
            install(bundled(PAYLOAD), places, version, register=False)

        with_progress(f"Instalando {APP_NAME} {version} (prueba)…", work)
        (target.parent / "ventana.txt").write_text(str(shown[0]), encoding="utf-8")
        return 0
    needs_webview2 = not webview2_installed()
    question = (
        f"Se va a instalar {APP_NAME} {version} para tu usuario (no hacen falta permisos de "
        "administrador), con accesos directos en el menú Inicio y en el escritorio.\n\n"
    )
    if needs_webview2:
        question += (
            "A este PC le falta WebView2, el componente de Microsoft con el que se dibuja la "
            "ventana de Erizo Coach: se descargará de Microsoft y se instalará (unos minutos; "
            "hace falta internet).\n\n"
        )
    question += "¿Quieres instalarlo?"
    if message(question, MB_YESNO | MB_ICONINFO) != IDYES:
        return 0

    def work(say: Callable[[str], None]) -> tuple[Path, str | None]:
        exe = install(bundled(PAYLOAD), default_places(), version)
        if not needs_webview2:
            return exe, None
        say(
            "Descargando e instalando WebView2 de Microsoft…\n"
            "Puede tardar unos minutos según tu conexión."
        )
        try:
            with tempfile.TemporaryDirectory(prefix="lolcoach-") as tmp:
                install_webview2(Path(tmp))
        except Exception as exc:
            return exe, str(exc)
        return exe, None

    try:
        exe, webview2_problem = with_progress(f"Instalando {APP_NAME} {version}…", work)
    except InstallError as exc:
        message(str(exc), MB_OK | MB_ICONWARN)
        return 1
    except Exception as exc:  # p. ej., sin permiso para escribir: que se vea el motivo
        message(f"No se ha podido instalar: {exc}", MB_OK | MB_ICONERROR)
        return 1
    done = f"{APP_NAME} {version} está instalado.\n\n"
    if webview2_problem:
        done += (
            f"No se ha podido instalar WebView2 ({webview2_problem}). No pasa nada: Erizo Coach "
            "abrirá su ventana con Microsoft Edge y funciona igual. Si quieres la ventana "
            "propia, vuelve a abrir este instalador con internet.\n\n"
        )
    done += (
        "Ábrelo cuando quieras desde el menú Inicio o el acceso directo del escritorio. "
        "Se desinstala desde Configuración > Aplicaciones > Aplicaciones instaladas.\n\n"
        "¿Quieres abrirlo ahora?"
    )
    if message(done, MB_YESNO | MB_ICONINFO) == IDYES:
        # A través del Explorador: así la app no hereda el entorno temporal del instalador.
        subprocess.Popen(["explorer.exe", str(exe)], creationflags=CREATE_NO_WINDOW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
