"""Ayudas para las pruebas del almacén de partidas: llenarlo y dañarlo como en el incidente."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from erizocoach.stats.extract import extract_builds
from erizocoach.stats.store import MatchStore

from .fake_riot_api import build_match

PAGE = 4096


def fill_store(path: Path, matches: int = 30, patch: str = "16.22") -> None:
    store = MatchStore(path)
    for n in range(matches):
        match, timeline = build_match(f"EUW1_{n:04d}", f"{patch}.1.1")
        store.add_match(f"EUW1_{n:04d}", patch, extract_builds(match, timeline))
    store.close()


def corrupt_database(path: Path) -> bytes:
    """Páginas ajenas en mitad del árbol de partidas (como las que dejó el contenedor de Claude)."""
    data = bytearray(path.read_bytes())
    data[PAGE : 2 * PAGE] = b"\x0d" + b"\x55" * (PAGE - 1)  # la raíz de «matches», ilegible
    path.write_bytes(data)
    return bytes(data)


def corruption_error(folder: Path) -> sqlite3.DatabaseError:
    """El error real que da SQLite al leer una base dañada («database disk image is malformed»)."""
    path = folder / "danada.sqlite3"
    fill_store(path)
    corrupt_database(path)
    db = sqlite3.connect(path)
    try:
        db.execute("SELECT COUNT(*) FROM matches WHERE builds IS NOT NULL").fetchone()
    except sqlite3.DatabaseError as exc:
        return exc
    finally:
        db.close()
    raise AssertionError("la base debería estar dañada")
