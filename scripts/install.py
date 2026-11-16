"""Instala Erizo Coach para tu usuario, con accesos directos en el menú Inicio y el escritorio.

Uso:
    uv run python scripts/build_exe.py     (antes: construir los ejecutables)
    uv run python scripts/install.py       instalar o actualizar
    uv run python scripts/install.py --desinstalar

* Se instala en %LOCALAPPDATA%\\Programs\\Erizo Coach (no hacen falta permisos de administrador).
* Tus datos (estadísticas, configuración, registros) están en %LOCALAPPDATA%\\ErizoCoach y
  no se tocan ni al actualizar ni al desinstalar.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "dist" / "Erizo Coach"
TARGET = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Erizo Coach"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
SHORTCUT_NAME = "Erizo Coach.lnk"
EXE_NAME = "Erizo Coach.exe"
# Hasta la 0.18 la app se llamaba «LoL Coach»: al instalar se quita esa instalación antigua.
LEGACY_TARGET = TARGET.with_name("LoL Coach")
LEGACY_SHORTCUT = "LoL Coach.lnk"
LEGACY_EXE = "LoL Coach.exe"


def desktop_folder() -> Path:
    """El escritorio de verdad (puede estar en OneDrive)."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return Path(result.stdout.strip())


def create_shortcut(link: Path, target: Path) -> None:
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LC_LINK); "
        "$s.TargetPath = $env:LC_TARGET; $s.WorkingDirectory = $env:LC_DIR; "
        "$s.IconLocation = $env:LC_TARGET + ',0'; "
        "$s.Description = 'Erizo Coach: runas, hechizos y builds para League of Legends'; "
        "$s.Save()"
    )
    env = {**os.environ, "LC_LINK": str(link), "LC_TARGET": str(target),
           "LC_DIR": str(target.parent)}  # fmt: skip
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)


def running() -> bool:
    names = {EXE_NAME.lower(), LEGACY_EXE.lower()}
    folders = (str(TARGET).lower() + os.sep, str(LEGACY_TARGET).lower() + os.sep)
    for proc in psutil.process_iter(["name", "exe"]):
        exe = (proc.info.get("exe") or "").lower()
        if (proc.info.get("name") or "").lower() in names and exe.startswith(folders):
            return True
    return False


def remove_legacy() -> list[str]:
    """Quita la app con el nombre antiguo (carpeta y accesos directos). Los datos se quedan."""
    removed = []
    for link in (START_MENU / LEGACY_SHORTCUT, desktop_folder() / LEGACY_SHORTCUT):
        if link.exists():
            link.unlink()
            removed.append(str(link))
    if (LEGACY_TARGET / LEGACY_EXE).is_file():
        shutil.rmtree(LEGACY_TARGET)
        removed.append(str(LEGACY_TARGET))
    return removed


def install() -> int:
    if not (SOURCE / EXE_NAME).exists():
        print("No hay ejecutables: primero «uv run python scripts/build_exe.py».")
        return 1
    if running():
        print("Erizo Coach está abierto: ciérralo y vuelve a intentarlo.")
        return 1
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    exe = TARGET / EXE_NAME
    links = [START_MENU / SHORTCUT_NAME, desktop_folder() / SHORTCUT_NAME]
    for link in links:
        link.parent.mkdir(parents=True, exist_ok=True)
        create_shortcut(link, exe)
    print(f"Erizo Coach instalado en {TARGET}")
    for link in links:
        print(f"  Acceso directo: {link}")
    for path in remove_legacy():
        print(f"  Quitado (nombre antiguo): {path}")
    return 0


def uninstall() -> int:
    if running():
        print("Erizo Coach está abierto: ciérralo y vuelve a intentarlo.")
        return 1
    removed = []
    for link in (START_MENU / SHORTCUT_NAME, desktop_folder() / SHORTCUT_NAME):
        if link.exists():
            link.unlink()
            removed.append(str(link))
    if TARGET.exists():
        shutil.rmtree(TARGET)
        removed.append(str(TARGET))
    print("Eliminado:" if removed else "No estaba instalado.")
    for path in removed:
        print(f"  {path}")
    print("Tus datos (%LOCALAPPDATA%\\ErizoCoach) se conservan.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Instala o desinstala Erizo Coach.")
    parser.add_argument("--desinstalar", action="store_true", help="quita Erizo Coach")
    args = parser.parse_args()
    return uninstall() if args.desinstalar else install()


if __name__ == "__main__":
    sys.exit(main())
