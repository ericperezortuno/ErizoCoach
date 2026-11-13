"""Prepara las estadísticas al conectar con el cliente y las pasa a la fuente de builds.

* Usa el parche de tu cliente. Si para ese parche aún hay pocas partidas (recién
  salido), usa el anterior si tiene más datos.
* La agregación se guarda en caché y solo se recalcula si hay partidas nuevas.
* Todo el trabajo pesado se hace en segundo plano, sin bloquear la app.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient
from ..lcu.connector import LcuConnector
from ..models import ClientInfo
from .aggregate import ChampionStats, aggregate, is_fresh, load_stats, save_stats
from .extract import patch_key
from .source import StatsBuildSource
from .store import DATABASE, MatchStore, is_corrupt, set_aside

log = logging.getLogger(__name__)
ui = logging.getLogger("lolcoach.ui")

ENOUGH_MATCHES = 100  # por debajo, se prefiere el parche anterior si tiene más datos


def stats_file(stats_dir: Path, patch: str) -> Path:
    return stats_dir / f"builds-{patch}.json"


Stats = tuple[str, int, dict[tuple[int, str], ChampionStats]]


def prepare_stats(stats_dir: Path, patch: str, gamedata: GameData) -> Stats | None:
    """Elige el parche, agrega (o lee la caché) y devuelve las estadísticas. Bloqueante.

    Con la base dañada no falla: la aparta (sin borrarla) y devuelve None (reglas).
    """
    database = stats_dir / DATABASE
    if not database.exists():
        return None
    try:
        return _prepare(database, stats_dir, patch, gamedata)
    except sqlite3.DatabaseError as exc:
        if not is_corrupt(exc):
            raise
        log.warning("La base de estadísticas se ha dañado: %s", exc)
        try:
            set_aside(database)
        except (OSError, sqlite3.DatabaseError) as err:  # abierta en otro programa: luego
            log.warning("No se ha podido apartar la base dañada todavía: %s", err)
        return None


def _prepare(database: Path, stats_dir: Path, patch: str, gamedata: GameData) -> Stats | None:
    store = MatchStore(database)  # si está dañada al abrirla, la aparta y empieza otra
    try:
        counts = dict(store.patches())
        chosen = patch
        if counts.get(patch, 0) < ENOUGH_MATCHES:
            older = [p for p in counts if patch_key(p) < patch_key(patch)]
            previous = max(older, key=patch_key, default=None)
            if previous and counts[previous] > counts.get(patch, 0):
                chosen = previous
        matches = counts.get(chosen, 0)
        if not matches:
            return None
        path = stats_file(stats_dir, chosen)
        loadouts = store.loadout_count(chosen)
        if is_fresh(path, matches, loadouts) and (cached := load_stats(path)) is not None:
            return cached
        stats = aggregate(store.builds(chosen), gamedata.item)
        save_stats(path, chosen, matches, stats, loadouts)
        return chosen, matches, stats
    finally:
        store.close()


class StatsService:
    def __init__(
        self,
        connector: LcuConnector,
        gamedata: GameData,
        source: StatsBuildSource,
        stats_dir: Path,
    ) -> None:
        self._gamedata = gamedata
        self._source = source
        self._stats_dir = stats_dir
        connector.on_connect(self._on_connect)

    async def _on_connect(self, _client: LcuClient, info: ClientInfo) -> None:
        await self.refresh(info.patch)

    async def refresh(self, patch: str) -> None:
        try:
            result = await asyncio.to_thread(prepare_stats, self._stats_dir, patch, self._gamedata)
        except (sqlite3.DatabaseError, OSError) as exc:  # p. ej., ocupada: se sigue igual
            log.warning("No se han podido leer las estadísticas: %s", exc)
            return
        if result is None:
            log.debug("Sin estadísticas descargadas: se usan las builds por reglas")
            return
        chosen, matches, stats = result
        self._source.update(chosen, matches, stats)
        ui.info(
            "Builds con estadísticas reales: %d campeones/posiciones con datos (%d partidas, "
            "parche %s)",
            self._source.covered,
            matches,
            chosen,
        )
