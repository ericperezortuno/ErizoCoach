# Especificación de PyInstaller para Erizo Coach.
#
# Crea dist/Erizo Coach/ con dos ejecutables que comparten las mismas librerías:
#   Erizo Coach.exe        la aplicación de escritorio (con icono, sin consola)
#   erizocoach-consola.exe los comandos de texto (stats clave, stats descargar...)
#
# Uso:  uv run python scripts/build_exe.py
# ruff: noqa

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "assets" / "erizocoach.ico")

datas = collect_data_files("erizocoach")  # certificado de Riot e interfaz (HTML/CSS/JS)
hiddenimports = (
    collect_submodules("erizocoach")
    + collect_submodules("keyring.backends")  # Administrador de credenciales de Windows
    + ["win32ctypes.core", "webview.platforms.winforms", "webview.platforms.edgechromium"]
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "trustme", "PIL", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

app = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Erizo Coach",
    icon=ICON,
    version=str(ROOT / "packaging" / "version_info.txt"),
    console=False,
    upx=False,
)
cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="erizocoach-consola",
    icon=ICON,
    version=str(ROOT / "packaging" / "version_info.txt"),
    console=True,
    upx=False,
)
COLLECT(app, cli, a.binaries, a.datas, name="Erizo Coach", upx=False)
