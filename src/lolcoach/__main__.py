"""Punto de entrada: ``lolcoach`` o ``python -m lolcoach``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .app import Application, run_check
from .config import load_settings
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings, warnings = load_settings(league_dir=args.league_dir, debug=args.debug or None)
    setup_logging(settings.log_dir, debug=settings.debug)
    for warning in warnings:
        log.warning(warning)

    if args.check:
        return asyncio.run(run_check(settings))

    lock = SingleInstanceLock(settings.lock_path)
    if not lock.acquire():
        log.error("LoL Coach ya está abierto en otra ventana.")
        return 1
    try:
        log.info("LoL Coach %s iniciado. Pulsa Ctrl+C para salir.", __version__)
        asyncio.run(Application(settings).run())
    except KeyboardInterrupt:
        log.info("LoL Coach cerrado. ¡Hasta la próxima!")
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
