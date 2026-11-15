"""Construye el instalador de un solo archivo para pasárselo a otra persona.

Uso:
    uv run python scripts/build_exe.py         (antes: construir la app en dist/Erizo Coach/)
    uv run python scripts/build_installer.py   crea dist/Instalar Erizo Coach <versión>.exe

El instalador lleva la app comprimida dentro; ver ``packaging/installer.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "Erizo Coach"
DIST = ROOT / "dist"
ICON = ROOT / "assets" / "erizocoach.ico"
INSTALLER = ROOT / "packaging" / "installer.py"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"

sys.path.insert(0, str(ROOT / "src"))
from lolcoach import __version__  # noqa: E402


def make_payload(app: Path, payload: Path) -> int:
    """Comprime la app (rutas relativas a su carpeta). Devuelve cuántos archivos lleva."""
    files = sorted(f for f in app.rglob("*") if f.is_file())
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in files:
            archive.write(file, file.relative_to(app).as_posix())
    return len(files)


def tcl_env() -> dict[str, str]:
    """Entorno para PyInstaller con Tcl/Tk localizables.

    Con el Python de python.org usado por uv, Tcl no encuentra sus archivos y PyInstaller
    quitaría ``tkinter`` (la ventanita de progreso del instalador).
    """
    env = dict(os.environ)
    base = Path(sys.base_prefix) / "tcl"
    libraries = (("TCL_LIBRARY", "tcl8.6", "init.tcl"), ("TK_LIBRARY", "tk8.6", "tk.tcl"))
    for variable, folder, marker in libraries:
        if variable not in env and (base / folder / marker).exists():
            env[variable] = str(base / folder)
    return env


def main() -> int:
    if not (APP / "Erizo Coach.exe").exists():
        print("No hay app construida: primero «uv run python scripts/build_exe.py».")
        return 1
    name = f"Instalar Erizo Coach {__version__}"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        payload = work / "payload.zip"
        count = make_payload(APP, payload)
        (work / "version.txt").write_text(__version__, encoding="utf-8")
        command = [
            sys.executable, "-m", "PyInstaller", str(INSTALLER),
            "--onefile", "--noconsole", "--noconfirm", "--clean", "--log-level", "WARN",
            "--name", name, "--icon", str(ICON), "--version-file", str(VERSION_INFO),
            "--add-data", f"{payload};.", "--add-data", f"{work / 'version.txt'};.",
            "--add-data", f"{ICON};.",
            "--distpath", str(DIST), "--workpath", str(work / "build"),
            "--specpath", str(work),
        ]  # fmt: skip
        result = subprocess.run(command, cwd=ROOT, env=tcl_env(), check=False)
    if result.returncode != 0:
        print("La construcción del instalador ha fallado.")
        return result.returncode
    exe = DIST / f"{name}.exe"
    print(f"Listo: {exe} ({exe.stat().st_size / 1_000_000:.0f} MB, {count} archivos de la app)")
    shutil.rmtree(ROOT / "build" / name, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
