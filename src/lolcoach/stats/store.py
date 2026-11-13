"""Almacén local de partidas descargadas (SQLite, en tu carpeta de datos).

Permite reanudar la descarga donde se dejó (las claves de desarrollo caducan cada
24 h) y no descargar dos veces la misma partida. Los identificadores de jugadores
de alto elo (PUUID, públicos en la API) solo se guardan aquí, para saber a quién
pedir partidas; nunca salen de tu PC.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from .extract import PlayerBuild, patch_key

DETAIL_ITEMS = 1  # partidas guardadas antes de recoger runas y hechizos
DETAIL_LOADOUT = 2  # con runas y hechizos
BUSY_TIMEOUT = 30  # segundos: la app y la consola pueden usar la base de datos a la vez

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    patch TEXT NOT NULL,
    collected_at INTEGER NOT NULL,
    builds TEXT,  -- NULL: partida descartada (otro parche, demasiado corta...)
    detail INTEGER NOT NULL DEFAULT 1  -- 1: solo objetos; 2: también runas y hechizos
);
CREATE INDEX IF NOT EXISTS matches_patch ON matches(patch);
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,  -- PUUID (o "sid:<id>" si la liga no lo trae)
    tier TEXT NOT NULL,
    crawled_at INTEGER  -- NULL: aún no consultado
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class MatchStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, timeout=BUSY_TIMEOUT)
        self._db.execute("PRAGMA journal_mode=WAL")  # lecturas sin bloquear la descarga
        self._db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(matches)")}
        if "detail" not in columns:  # bases de datos de la v0.5.0
            with self._db:
                self._db.execute("ALTER TABLE matches ADD COLUMN detail INTEGER NOT NULL DEFAULT 1")

    def close(self) -> None:
        self._db.close()

    # --- Partidas ------------------------------------------------------------------

    def has_match(self, match_id: str) -> bool:
        row = self._db.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        return row is not None

    def add_match(self, match_id: str, patch: str, builds: list[PlayerBuild] | None) -> None:
        payload = json.dumps([b.to_json() for b in builds]) if builds else None
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO matches VALUES (?, ?, ?, ?, ?)",
                (match_id, patch, int(time.time()), payload, DETAIL_LOADOUT),
            )

    def matches_without_loadout(self, patch: str, limit: int = 100) -> list[str]:
        """Partidas del parche guardadas sin runas ni hechizos (descargadas con la v0.5.0)."""
        query = (
            "SELECT match_id FROM matches WHERE patch = ? AND builds IS NOT NULL "
            "AND detail < ? LIMIT ?"
        )
        return [row[0] for row in self._db.execute(query, (patch, DETAIL_LOADOUT, limit))]

    def match_builds(self, match_id: str) -> list[PlayerBuild]:
        row = self._db.execute(
            "SELECT builds FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        if row is None or row[0] is None:
            return []
        return [PlayerBuild.from_json(entry) for entry in json.loads(row[0])]

    def complete_loadout(self, match_id: str, builds: list[PlayerBuild] | None) -> None:
        """Guarda las builds completadas con runas y hechizos (o marca que no se pudo)."""
        with self._db:
            if builds:
                self._db.execute(
                    "UPDATE matches SET builds = ?, detail = ? WHERE match_id = ?",
                    (json.dumps([b.to_json() for b in builds]), DETAIL_LOADOUT, match_id),
                )
            else:  # la partida ya no está disponible: se deja con solo los objetos
                self._db.execute(
                    "UPDATE matches SET detail = ? WHERE match_id = ?", (DETAIL_LOADOUT, match_id)
                )

    def prune(self, keep_from: str) -> int:
        """Borra las partidas de parches anteriores a ``keep_from``. Devuelve cuántas."""
        old = [p for (p,) in self._db.execute("SELECT DISTINCT patch FROM matches")
               if patch_key(p) < patch_key(keep_from)]  # fmt: skip
        if not old:
            return 0
        with self._db:
            cursor = self._db.executemany(
                "DELETE FROM matches WHERE patch = ?", [(p,) for p in old]
            )
        return cursor.rowcount

    def builds(self, patch: str) -> Iterator[PlayerBuild]:
        query = "SELECT builds FROM matches WHERE patch = ? AND builds IS NOT NULL"
        for (payload,) in self._db.execute(query, (patch,)):
            for entry in json.loads(payload):
                yield PlayerBuild.from_json(entry)

    def match_count(self, patch: str) -> int:
        query = "SELECT COUNT(*) FROM matches WHERE patch = ? AND builds IS NOT NULL"
        return int(self._db.execute(query, (patch,)).fetchone()[0])

    def loadout_count(self, patch: str) -> int:
        """Partidas del parche con runas y hechizos."""
        query = (
            "SELECT COUNT(*) FROM matches WHERE patch = ? AND builds IS NOT NULL AND detail >= ?"
        )
        return int(self._db.execute(query, (patch, DETAIL_LOADOUT)).fetchone()[0])

    def patches(self) -> list[tuple[str, int]]:
        query = (
            "SELECT patch, COUNT(*) FROM matches WHERE builds IS NOT NULL "
            "GROUP BY patch ORDER BY patch DESC"
        )
        return [(p, int(n)) for p, n in self._db.execute(query)]

    # --- Jugadores ---------------------------------------------------------------------

    def add_players(self, players: list[tuple[str, str]]) -> int:
        with self._db:
            cursor = self._db.executemany(
                "INSERT OR IGNORE INTO players (player_id, tier) VALUES (?, ?)", players
            )
        return cursor.rowcount

    def player_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM players").fetchone()[0])

    def players_to_crawl(self, limit: int = 100_000) -> list[str]:
        """Primero los nunca consultados (en orden aleatorio), luego los más antiguos."""
        query = (
            "SELECT player_id FROM players "
            "ORDER BY crawled_at IS NOT NULL, crawled_at, RANDOM() LIMIT ?"
        )
        return [row[0] for row in self._db.execute(query, (limit,))]

    def mark_crawled(self, player_id: str, new_id: str | None = None) -> None:
        with self._db:
            if new_id and new_id != player_id:
                self._db.execute("DELETE FROM players WHERE player_id = ?", (player_id,))
                self._db.execute(
                    "INSERT OR REPLACE INTO players VALUES (?, 'resolved', ?)",
                    (new_id, int(time.time())),
                )
            else:
                self._db.execute(
                    "UPDATE players SET crawled_at = ? WHERE player_id = ?",
                    (int(time.time()), player_id),
                )

    # --- Metadatos ---------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
