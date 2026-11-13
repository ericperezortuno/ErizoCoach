"""Prepara las estadísticas al conectar con el cliente y las pasa a la fuente de builds.

* Usa el parche de tu cliente. Si para ese parche aún hay pocas partidas (recién
  salido), usa el anterior si tiene más datos.
* La agregación se guarda en caché y solo se recalcula si hay partidas nuevas.
* Todo el trabajo pesado se hace en segundo plano, sin bloquear la app.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient
from ..lcu.connector import LcuConnector
from ..models import ClientInfo
from .aggregate import ChampionStats, aggregate, load_stats, save_stats
from .extract import patch_key
from .source import StatsBuildSource
from .store import MatchStore

log = logging.getLogger(__name__)
ui = logging.getLogger("lolcoach.ui")

ENOUGH_MATCHES = 100  # por debajo, se prefiere el parche anterior si tiene más datos


def stats_file(stats_dir: Path, patch: str) -> Path:
    return stats_dir / f"builds-{patch}.json"


def prepare_stats(
    stats_dir: Path, patch: str, gamedata: GameData
) -> tuple[str, int, dict[tuple[int, str], ChampionStats]] | None:
    """Elige el parche, agrega (o lee la caché) y devuelve las estadísticas. Bloqueante."""
    database = stats_dir / "matches.sqlite3"
    if not database.exists():
        return None
    store = MatchStore(database)
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
        cached = load_stats(path)
        if cached is not None and cached[1] == matches:
            return cached
        stats = aggregate(store.builds(chosen), gamedata.item)
        save_stats(path, chosen, matches, stats)
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
        result = await asyncio.to_thread(prepare_stats, self._stats_dir, patch, self._gamedata)
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
