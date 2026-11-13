"""Instalador de LoL Coach en un solo archivo (para pasárselo a otra persona).

Se construye con ``uv run python scripts/build_installer.py`` y lleva dentro la app
(``payload.zip``). Al abrirlo:

* Pregunta si quieres instalar y lo instala **solo para tu usuario**, sin permisos de
  administrador, en ``%LOCALAPPDATA%\\Programs\\LoL Coach``.
* Crea accesos directos en el menú Inicio y en el escritorio.
* Aparece en *Configuración > Aplicaciones > Aplicaciones instaladas*, desde donde se
  desinstala (se quitan la app y los accesos directos; los datos se conservan).
* Si ya estaba instalado, lo actualiza. Si LoL Coach está abierto, pide cerrarlo antes.

Solo usa la biblioteca estándar de Python (y ``ctypes`` para las ventanas de Windows).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "LoL Coach"
EXE_NAME = "LoL Coach.exe"
SHORTCUT_NAME = "LoL Coach.lnk"
UNINSTALL_SCRIPT = "desinstalar.ps1"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LoLCoach"
PAYLOAD = "payload.zip"
VERSION_FILE = "version.txt"  # dentro del paquete: la versión que se instala

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
if (Get-Process -Name 'LoL Coach' | Where-Object { $_.Path -like "$app\*" }) {
    $box::Show('LoL Coach está abierto: ciérralo y vuelve a desinstalarlo.', 'LoL Coach')
    exit 1
}
$question = '¿Quitar LoL Coach de este PC? Tus datos (estadísticas y ajustes) se conservan.'
if ($box::Show($question, 'Desinstalar LoL Coach', 'YesNo') -ne 'Yes') { exit 0 }
foreach ($folder in 'Programs', 'Desktop') {
    $link = Join-Path ([Environment]::GetFolderPath($folder)) 'LoL Coach.lnk'
    Remove-Item -LiteralPath $link -Force
}
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LoLCoach'
Remove-Item -LiteralPath $key -Recurse -Force
Set-Location -LiteralPath $env:TEMP
Remove-Item -LiteralPath $app -Recurse -Force
$box::Show('LoL Coach se ha desinstalado.', 'LoL Coach') | Out-Null
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


def app_running(target: Path) -> bool:
    """¿Está abierto el LoL Coach instalado en ``target``? (sin librerías extra)."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name 'LoL Coach' -ErrorAction SilentlyContinue | "
         "ForEach-Object { $_.Path }"],
        capture_output=True, text=True, check=False, creationflags=CREATE_NO_WINDOW,
    )  # fmt: skip
    prefix = str(target).lower() + os.sep
    return any(line.strip().lower().startswith(prefix) for line in result.stdout.splitlines())


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
        "$s.Description = 'LoL Coach: runas, hechizos y builds para League of Legends'; "
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


def install(payload: Path, places: Places, version: str, *, register: bool = True) -> Path:
    """Instala o actualiza. Devuelve la ruta del ejecutable."""
    if app_running(places.target):
        raise InstallError("LoL Coach está abierto: ciérralo y vuelve a abrir el instalador.")
    extract(payload, places.target)
    exe = places.target / EXE_NAME
    for folder in places.shortcuts:
        folder.mkdir(parents=True, exist_ok=True)
        create_shortcut(folder / SHORTCUT_NAME, exe)
    if register:
        register_uninstaller(places.target, version)
    return exe


# --- Ventanas -----------------------------------------------------------------------------


def message(text: str, flags: int = MB_OK | MB_ICONINFO) -> int:
    import ctypes

    return int(ctypes.windll.user32.MessageBoxW(None, text, f"Instalar {APP_NAME}", flags))


def bundled(name: str) -> Path:
    """Archivo incluido en el instalador (PyInstaller lo descomprime en ``_MEIPASS``)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    version = bundled(VERSION_FILE).read_text(encoding="utf-8").strip()
    if len(args) == 2 and args[0] == "--destino":
        # Prueba sin ventanas: instala en una carpeta, con los accesos directos dentro y sin
        # tocar el registro (para comprobar el instalador sin cambiar tu instalación).
        target = Path(args[1]).resolve() / APP_NAME
        places = Places(target=target, shortcuts=(target.parent / "accesos",))
        install(bundled(PAYLOAD), places, version, register=False)
        return 0
    question = (
        f"Se va a instalar {APP_NAME} {version} para tu usuario (no hacen falta permisos de "
        "administrador), con accesos directos en el menú Inicio y en el escritorio.\n\n"
        "¿Quieres instalarlo?"
    )
    if message(question, MB_YESNO | MB_ICONINFO) != IDYES:
        return 0
    try:
        exe = install(bundled(PAYLOAD), default_places(), version)
    except InstallError as exc:
        message(str(exc), MB_OK | MB_ICONWARN)
        return 1
    except Exception as exc:  # p. ej., sin permiso para escribir: que se vea el motivo
        message(f"No se ha podido instalar: {exc}", MB_OK | MB_ICONERROR)
        return 1
    done = (
        f"{APP_NAME} {version} está instalado.\n\n"
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
