"""Punto de entrada: ``lolcoach``, ``python -m lolcoach`` o ``LoL Coach.exe``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path

from . import __version__
from .app import Application, UiMode, run_check
from .config import Settings, load_settings
from .logs import setup_logging
from .single_instance import SingleInstanceLock

log = logging.getLogger("lolcoach.ui")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lolcoach", description="Asistente local para League of Legends."
    )
    parser.add_argument(
        "--check", action="store_true", help="comprueba la conexión con el cliente y sale"
    )
    parser.add_argument(
        "--debug", action="store_true", help="muestra también los mensajes técnicos"
    )
    parser.add_argument(
        "--league-dir", type=Path, help="carpeta de LoL, si no se detecta automáticamente"
    )
    parser.add_argument("--no-window", action="store_true", help="sin ventana (solo consola)")
    parser.add_argument(
        "--browser", action="store_true", help="abre la ventana con Edge en lugar de escritorio"
    )
    parser.add_argument(
        "--demo", action="store_true", help="muestra la ventana con una partida de ejemplo"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    commands = parser.add_subparsers(dest="command", metavar="comando")
    stats = commands.add_parser(
        "stats", help="estadísticas de partidas reales de alto elo (API oficial de Riot)"
    )
    stats_commands = stats.add_subparsers(dest="stats_command", metavar="acción", required=True)
    stats_commands.add_parser("clave", help="guarda tu clave de la API de Riot (cifrada)")
    download = stats_commands.add_parser(
        "descargar", help="descarga partidas de alto elo del parche actual"
    )
    download.add_argument(
        "--horas", type=float, default=2.0, help="tiempo máximo de descarga (por defecto 2)"
    )
    download.add_argument("--partidas", type=int, help="número máximo de partidas nuevas")
    stats_commands.add_parser("estado", help="muestra cuántas partidas hay descargadas")
    stats_commands.add_parser("olvidar-clave", help="borra tu clave de Riot guardada")
    stats_commands.add_parser(
        "publicar", help="publica ya tus estadísticas para quien no tiene clave"
    )
    return parser


def run_stats(args: argparse.Namespace, settings: Settings) -> int:
    from .stats import cli

    if args.stats_command == "clave":
        return cli.cmd_key(settings)
    if args.stats_command == "descargar":
        return cli.cmd_update(settings, args.horas, args.partidas)
    if args.stats_command == "olvidar-clave":
        return cli.cmd_forget_key()
    if args.stats_command == "publicar":
        return cli.cmd_publish(settings)
    return cli.cmd_status(settings)


def choose_ui_mode(settings: Settings, *, demo: bool, browser: bool) -> UiMode:
    if not settings.window:
        return UiMode.SERVER_ONLY if demo else UiMode.NONE
    if browser or settings.window_mode == "navegador":
        return UiMode.BROWSER
    from .ui.desktop import desktop_available

    if desktop_available():
        return UiMode.DESKTOP
    log.info("No se puede abrir la ventana de escritorio (falta WebView2): se usa Edge.")
    return UiMode.BROWSER


def run_desktop(settings: Settings, *, demo: bool) -> bool:
    """La app trabaja en segundo plano y la ventana ocupa el hilo principal.

    Devuelve ``False`` si la ventana de escritorio no ha llegado a abrirse (por ejemplo,
    porque WebView2 o .NET fallan en ese PC): entonces se abre con Edge.
    """
    from .ui.desktop import DesktopWindow

    app = Application(settings, demo=demo, ui_mode=UiMode.DESKTOP)
    worker = threading.Thread(target=_run_in_thread, args=(app,), name="lolcoach-app", daemon=True)
    worker.start()
    if not app.started.wait(timeout=15):
        log.error("LoL Coach no ha podido arrancar.")
        return True
    window = DesktopWindow(app, settings)
    try:
        window.run()  # vuelve al cerrar la ventana
    except Exception:
        if window.shown:
            raise
        log.exception("No se puede abrir la ventana de escritorio: se usa Edge.")
        return False
    finally:
        app.request_stop()
        worker.join(timeout=10)
    return True


def _run_in_thread(app: Application) -> None:
    try:
        asyncio.run(app.run())
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Error inesperado en LoL Coach")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings, warnings = load_settings(
        league_dir=args.league_dir,
        debug=args.debug or None,
        window_flag=False if args.no_window else None,
    )
    setup_logging(settings.log_dir, debug=settings.debug)
    for warning in warnings:
        log.warning(warning)

    if args.check:
        return asyncio.run(run_check(settings))
    if args.command == "stats":
        return run_stats(args, settings)

    lock = SingleInstanceLock(settings.lock_path)
    if not lock.acquire():
        from .ui.tray import signal_running

        if signal_running():  # estaba en segundo plano (o detrás): se abre su ventana
            log.info("LoL Coach ya estaba abierto: se muestra su ventana.")
            return 0
        message = "LoL Coach ya está abierto."
        log.error(message)
        _show_message(message)
        return 1
    mode = choose_ui_mode(settings, demo=args.demo, browser=args.browser)
    try:
        log.info("LoL Coach %s iniciado.", __version__)
        if mode == UiMode.DESKTOP and not run_desktop(settings, demo=args.demo):
            mode = UiMode.BROWSER
        if mode != UiMode.DESKTOP:
            log.info("Pulsa Ctrl+C para salir.")
            asyncio.run(Application(settings, demo=args.demo, ui_mode=mode).run())
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
        log.info("LoL Coach cerrado. ¡Hasta la próxima!")
    return 0


def _show_message(text: str) -> None:
    """Aviso en una ventanita (útil en el .exe, que no tiene consola)."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, "LoL Coach", 0x40)


if __name__ == "__main__":
    sys.exit(main())
