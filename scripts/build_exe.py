"""Construye los ejecutables de Erizo Coach en dist/Erizo Coach/.

Uso:  uv run python scripts/build_exe.py
Después, para instalarlo con accesos directos:  uv run python scripts/install.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "lolcoach.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def main() -> int:
    for folder in (DIST / "Erizo Coach", BUILD):
        shutil.rmtree(folder, ignore_errors=True)
    command = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", str(DIST), "--workpath", str(BUILD),
    ]  # fmt: skip
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        print("La construcción ha fallado.")
        return result.returncode
    folder = DIST / "Erizo Coach"
    size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_000_000
    print(f"Listo: {folder} ({size:.0f} MB)")
    for exe in ("Erizo Coach.exe", "erizocoach-consola.exe"):
        print(f"  {exe}: {'OK' if (folder / exe).exists() else 'FALTA'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
