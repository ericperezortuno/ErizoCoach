"""Almacén local de partidas descargadas (SQLite, en tu carpeta de datos).

Permite reanudar la descarga donde se dejó (las claves de desarrollo caducan cada
24 h) y no descargar dos veces la misma partida. Los identificadores de jugadores
de alto elo (PUUID, públicos en la API) solo se guardan aquí, para saber a quién
pedir partidas; nunca salen de tu PC.

Si la base se daña, no se arrastra el error en cada tanda: al abrirla se comprueba
(``PRAGMA quick_check``) y, si está dañada, se aparta sin borrarla
(``matches.corrupt-<fecha>.sqlite3``) y se empieza una nueva; las partidas se vuelven
a descargar solas.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from .extract import PlayerBuild, patch_key

log = logging.getLogger(__name__)

DATABASE = "matches.sqlite3"  # en la carpeta de estadísticas
DETAIL_LOADOUT = 2  # con runas y hechizos (1: guardadas antes de recogerlos, solo objetos)
BUSY_TIMEOUT = 30  # segundos: la app y la consola pueden usar la base de datos a la vez
SQLITE_CORRUPT, SQLITE_NOTADB = 11, 26  # códigos de SQLite: base dañada / no es una base
SIDE_FILES = ("-journal", "-wal", "-shm")  # archivos que acompañan a la base
QUARANTINE_NOTICE = "quarantined"  # meta: base dañada apartada, aún sin avisar en la ventana


class CorruptDatabaseError(sqlite3.DatabaseError):
    """La comprobación de la base (``PRAGMA quick_check``) encuentra daños."""


def is_corrupt(exc: BaseException) -> bool:
    """¿Es un daño de la base? (no un bloqueo pasajero ni un error de la consulta)."""
    if isinstance(exc, CorruptDatabaseError):
        return True
    code = getattr(exc, "sqlite_errorcode", None)
    return (
        isinstance(exc, sqlite3.DatabaseError)
        and isinstance(code, int)
        and code & 0xFF in (SQLITE_CORRUPT, SQLITE_NOTADB)
    )


def quarantine(path: Path) -> Path:
    """Aparta la base dañada sin borrarla y devuelve dónde ha quedado.

    Sus archivos acompañantes se mueven antes que la base: una base nueva nunca debe
    encontrarse el diario de la anterior. Si algo sigue abierto en otro programa, Windows
    no deja moverlo (``OSError``) y la base se queda donde está.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.stem}.corrupt-{stamp}-{n}{path.suffix}")
        n += 1
    for suffix in SIDE_FILES:
        side = Path(f"{path}{suffix}")
        if side.exists():
            side.rename(Path(f"{target}{suffix}"))
    path.rename(target)
    return target


def set_aside(path: Path) -> Path:
    """Aparta la base dañada y deja una nueva, vacía, con el aviso pendiente para la ventana."""
    moved = quarantine(path)
    store = MatchStore(path)
    store.set_meta(QUARANTINE_NOTICE, moved.name)
    store.close()
    log.warning("Base de estadísticas dañada: apartada como %s; se empieza una nueva", moved.name)
    return moved


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
    crawled_at INTEGER,  -- NULL: aún no consultado
    region TEXT NOT NULL DEFAULT 'euw1'  -- de qué región es (euw1, kr, na1...)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _connect(path: Path) -> sqlite3.Connection:
    """Conexión comprobada: lanza ``CorruptDatabaseError`` si la base está dañada.

    Se puede abrir en un hilo y usar en otro (la descarga la abre fuera del bucle de la
    app, porque la comprobación tarda): cada almacén lo usa una sola tarea a la vez.
    """
    db = sqlite3.connect(path, timeout=BUSY_TIMEOUT, check_same_thread=False)
    try:
        _use_rollback_journal(db)
        problems = [str(row[0]) for row in db.execute("PRAGMA quick_check(5)")]
        if problems != ["ok"]:
            raise CorruptDatabaseError("; ".join(problems)[:300])
        db.executescript(SCHEMA)
        columns = {row[1] for row in db.execute("PRAGMA table_info(matches)")}
        if "detail" not in columns:  # bases de datos de la v0.5.0
            with db:
                db.execute("ALTER TABLE matches ADD COLUMN detail INTEGER NOT NULL DEFAULT 1")
        if "region" not in {row[1] for row in db.execute("PRAGMA table_info(players)")}:
            with db:  # antes de la v0.15.0 solo se descargaba de EUW
                db.execute("ALTER TABLE players ADD COLUMN region TEXT NOT NULL DEFAULT 'euw1'")
    except BaseException:
        db.close()
        raise
    return db


def _use_rollback_journal(db: sqlite3.Connection) -> None:
    """Diario clásico en vez de WAL (las versiones 0.5–0.7 usaban WAL).

    Con WAL, los archivos ``-wal`` y ``-shm`` siguen abiertos durante toda una tanda de
    descarga. Un programa con el disco virtualizado (p. ej., uno lanzado desde el
    contenedor de Claude) ve su propia copia de la base pero esos dos archivos reales, y lo
    que escribe acaba mezclado en la base real. Con el diario clásico solo existe un
    ``-journal`` durante los milisegundos de cada escritura, y ningún archivo compartido.
    El coste: una escritura espera a que acabe una lectura larga (hay margen de sobra).
    """
    mode = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if mode == "delete":
        return
    db.execute("PRAGMA busy_timeout = 2000")  # el cambio necesita la base para sí sola
    try:
        db.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.OperationalError as exc:  # la tiene abierta otro programa: se cambia luego
        log.debug("Diario de la base sin cambiar todavía: %s", exc)
    finally:
        db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT * 1000}")


def players_refreshed_key(region: str) -> str:
    """Metadato con la última lectura de las ligas de una región."""
    return f"players_refreshed:{region}"


class MatchStore:
    def __init__(self, path: Path) -> None:
        """Abre (o crea) la base. Si está dañada, la aparta y empieza otra: ``quarantined``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.quarantined: Path | None = None
        try:
            self._db = _connect(path)
        except sqlite3.DatabaseError as exc:
            if not is_corrupt(exc):
                raise
            log.warning("La base de estadísticas está dañada (%s)", exc)
            self.quarantined = quarantine(path)
            self._db = _connect(path)
            self.set_meta(QUARANTINE_NOTICE, self.quarantined.name)
            log.warning("Apartada como %s; se empieza una nueva", self.quarantined.name)

    def close(self) -> None:
        self._db.close()

    def take_quarantine_notice(self) -> str | None:
        """Nombre de la base dañada que se apartó, si aún no se ha avisado (solo una vez)."""
        name = self.get_meta(QUARANTINE_NOTICE)
        if name is not None:
            with self._db:
                self._db.execute("DELETE FROM meta WHERE key = ?", (QUARANTINE_NOTICE,))
        return name

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

    def matches_without_loadout(
        self, patch: str, limit: int = 100, *, prefix: str = ""
    ) -> list[str]:
        """Partidas del parche guardadas sin runas ni hechizos (descargadas con la v0.5.0).

        ``prefix`` (p. ej. ``EUW1_``) se queda solo con las de una región.
        """
        query = (
            "SELECT match_id FROM matches WHERE patch = ? AND builds IS NOT NULL "
            "AND detail < ? AND substr(match_id, 1, ?) = ? LIMIT ?"
        )
        args = (patch, DETAIL_LOADOUT, len(prefix), prefix, limit)
        return [row[0] for row in self._db.execute(query, args)]

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

    def games(self, patch: str) -> Iterator[list[PlayerBuild]]:
        """Las builds de cada partida del parche, partida a partida (para emparejar líneas)."""
        query = "SELECT builds FROM matches WHERE patch = ? AND builds IS NOT NULL"
        for (payload,) in self._db.execute(query, (patch,)):
            yield [PlayerBuild.from_json(entry) for entry in json.loads(payload)]

    def match_count(self, patch: str) -> int:
        query = "SELECT COUNT(*) FROM matches WHERE patch = ? AND builds IS NOT NULL"
        return int(self._db.execute(query, (patch,)).fetchone()[0])

    def loadout_count(self, patch: str) -> int:
        """Partidas del parche con runas y hechizos."""
        query = (
            "SELECT COUNT(*) FROM matches WHERE patch = ? AND builds IS NOT NULL AND detail >= ?"
        )
        return int(self._db.execute(query, (patch, DETAIL_LOADOUT)).fetchone()[0])

    def matches_by_region(self, patch: str) -> dict[str, int]:
        """Partidas del parche por región (``EUW1``, ``KR``...), según el prefijo de su id."""
        query = (
            "SELECT substr(match_id, 1, instr(match_id, '_') - 1) AS region, COUNT(*) "
            "FROM matches WHERE patch = ? AND builds IS NOT NULL "
            "GROUP BY region ORDER BY COUNT(*) DESC, region"
        )
        return {region: int(n) for region, n in self._db.execute(query, (patch,))}

    def patches(self) -> list[tuple[str, int]]:
        query = (
            "SELECT patch, COUNT(*) FROM matches WHERE builds IS NOT NULL "
            "GROUP BY patch ORDER BY patch DESC"
        )
        return [(p, int(n)) for p, n in self._db.execute(query)]

    # --- Jugadores ---------------------------------------------------------------------

    def add_players(self, players: list[tuple[str, str]], region: str = "euw1") -> int:
        with self._db:
            cursor = self._db.executemany(
                "INSERT OR IGNORE INTO players (player_id, tier, region) VALUES (?, ?, ?)",
                [(player, tier, region) for player, tier in players],
            )
        return cursor.rowcount

    def player_count(self, region: str | None = None) -> int:
        if region is None:
            return int(self._db.execute("SELECT COUNT(*) FROM players").fetchone()[0])
        query = "SELECT COUNT(*) FROM players WHERE region = ?"
        return int(self._db.execute(query, (region,)).fetchone()[0])

    def players_by_region(self) -> dict[str, int]:
        query = "SELECT region, COUNT(*) FROM players GROUP BY region ORDER BY COUNT(*) DESC"
        return {region: int(n) for region, n in self._db.execute(query)}

    def forget_players(self, region: str = "euw1") -> int:
        """Olvida los jugadores de una región (p. ej., sus PUUID son de otra clave) para
        releer sus ligas."""
        with self._db:
            cursor = self._db.execute("DELETE FROM players WHERE region = ?", (region,))
            self._db.execute("DELETE FROM meta WHERE key = ?", (players_refreshed_key(region),))
        return cursor.rowcount

    def players_to_crawl(self, region: str = "euw1", limit: int = 100_000) -> list[str]:
        """Primero los nunca consultados, luego los consultados hace más tiempo. Dentro de cada
        grupo, antes Challenger, Gran Maestro y Maestro que Diamante (en orden aleatorio)."""
        query = (
            "SELECT player_id FROM players WHERE region = ? "
            "ORDER BY crawled_at IS NOT NULL, tier LIKE 'DIAMOND%', crawled_at, RANDOM() "
            "LIMIT ?"
        )
        return [row[0] for row in self._db.execute(query, (region, limit))]

    def mark_crawled(self, player_id: str, new_id: str | None = None) -> None:
        with self._db:
            if new_id and new_id != player_id:  # «sid:...» resuelto a su PUUID
                self._db.execute(
                    "UPDATE OR REPLACE players SET player_id = ?, tier = 'resolved', "
                    "crawled_at = ? WHERE player_id = ?",
                    (new_id, int(time.time()), player_id),
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
