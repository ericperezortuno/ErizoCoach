"""Paquete de estadísticas compartido: las builds, runas y hechizos ya calculados.

Quien tiene clave de Riot descarga las partidas y su Erizo Coach publica el resultado en
un repositorio público (ver ``publish.py``). Es un archivo pequeño (unos 30 KB) que solo
lleva agregados por campeón y posición: ningún nombre, PUUID ni partida concreta.

Quien no tiene clave lo descarga de ahí y usa las mismas builds. Como llega de internet,
se valida entero antes de usarlo: tamaño acotado (también descomprimido), formato
conocido y cada entrada con valores razonables; lo que no cuadra se descarta.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import re
import time
import zlib
from collections.abc import Callable
from pathlib import Path

import aiohttp

from ..models import LANE_POSITIONS
from .aggregate import RUNE_OPTIONS, ChampionStats, Stats, parse_payload, stats_payload
from .extract import RUNE_FIELDS

log = logging.getLogger(__name__)

DEFAULT_PACK_URL = (
    "https://raw.githubusercontent.com/ericperezortuno/LoLCoach-datos/main/estadisticas.json.gz"
)
PACK_FILE = "estadisticas.json.gz"  # nombre en el repositorio público
CACHE_FILE = "compartidas.json.gz"  # copia local de la última descarga buena
REFRESH_SECONDS = 6 * 3600  # se vuelve a descargar como mucho cada 6 horas
MAX_DOWNLOAD = 8_000_000  # bytes comprimidos (el paquete real ronda los 100-300 KB)
MAX_UNPACKED = 60_000_000  # bytes descomprimidos: nada de «bombas» de compresión
TIMEOUT = aiohttp.ClientTimeout(total=20)

POSITIONS = frozenset(p.api_name for p in LANE_POSITIONS)
PATCH = re.compile(r"\d{1,3}\.\d{1,3}")
MAX_ID = 1_000_000
MAX_GAMES = 10_000_000
MAX_MATCHUPS = 400  # rivales distintos en una línea: hay menos campeones que esto


class PackError(Exception):
    """El paquete descargado no es válido."""


def encode_pack(stats: Stats) -> bytes:
    payload = stats_payload(*stats)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)


def decode_pack(data: bytes) -> Stats:
    """Abre y valida un paquete. Lanza :class:`PackError` si no sirve."""
    if len(data) > MAX_DOWNLOAD:
        raise PackError("demasiado grande")
    try:
        inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)  # gzip
        raw = inflater.decompress(data, MAX_UNPACKED)
        if inflater.unconsumed_tail or not inflater.eof:
            raise PackError("incompleto o demasiado grande descomprimido")
        payload = json.loads(raw.decode("utf-8"))
    except (zlib.error, UnicodeDecodeError, ValueError) as exc:
        raise PackError(f"no se puede leer ({exc})") from exc
    parsed = parse_payload(payload, accept=is_sane)
    if parsed is None:
        raise PackError("formato desconocido (¿Erizo Coach desactualizado?)")
    patch, matches, builds = parsed
    if not PATCH.fullmatch(patch) or not 0 < matches <= MAX_GAMES or not builds:
        raise PackError("sin datos válidos")
    return parsed


def is_sane(entry: ChampionStats) -> bool:
    """Valores dentro de lo posible (lo que no, se descarta sin usar)."""
    items = (
        *(s.item_id for s in entry.starting),
        *entry.core,
        *entry.extra,
        *((entry.boots,) if entry.boots is not None else ()),
    )
    return (
        0 < entry.champion_id < MAX_ID
        and entry.position in POSITIONS
        and 0 <= entry.wins <= entry.games <= MAX_GAMES
        and 0 <= entry.loadout_games <= entry.games
        and 0.0 <= entry.boots_rate <= 1.0
        and len(entry.starting) <= 6
        and all(0 < s.count <= 10 for s in entry.starting)
        and len(entry.core) <= 3
        and len(entry.extra) <= 6
        and all(0 < i < MAX_ID for i in items)
        and len(entry.runes) in (0, RUNE_FIELDS)
        and all(0 < r < MAX_ID for r in entry.runes)
        and len(entry.spells) in (0, 2)
        and all(0 < s < MAX_ID for s in entry.spells)
        and len(entry.rune_options) <= RUNE_OPTIONS
        and all(_sane_option(option, entry.loadout_games) for option in entry.rune_options)
        and len(entry.matchups) <= MAX_MATCHUPS
        and all(
            0 < rival < MAX_ID and 0 < games <= entry.games and 0 <= wins <= games
            for rival, games, wins in entry.matchups
        )
    )


def _sane_option(option: tuple[int, ...], loadout_games: int) -> bool:
    """(partidas, victorias, 11 campos de runas)."""
    return (
        len(option) == 2 + RUNE_FIELDS
        and 0 < option[0] <= loadout_games
        and 0 <= option[1] <= option[0]
        and all(0 < r < MAX_ID for r in option[2:])
    )


def read_cache(path: Path) -> Stats | None:
    try:
        return decode_pack(path.read_bytes())
    except (OSError, PackError):
        return None


async def download_pack(url: str) -> bytes:
    """Descarga el paquete (sin seguir más allá de :data:`MAX_DOWNLOAD` bytes)."""
    async with (
        aiohttp.ClientSession(timeout=TIMEOUT) as session,
        session.get(url, headers={"Accept-Encoding": "identity"}) as response,
    ):
        if response.status != 200:
            raise PackError(f"HTTP {response.status}")
        data = bytearray()
        async for chunk in response.content.iter_chunked(65_536):
            data += chunk
            if len(data) > MAX_DOWNLOAD:
                raise PackError("demasiado grande")
        return bytes(data)


class SharedStats:
    """Las estadísticas compartidas: descarga, caché local y validación."""

    def __init__(
        self,
        url: str,
        stats_dir: Path,
        *,
        refresh_seconds: float = REFRESH_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._url = url
        self._cache = stats_dir / CACHE_FILE
        self._refresh = refresh_seconds
        self._now = clock
        self._loaded = False
        self._checked = float("-inf")  # última descarga buena o intento
        self._stats: Stats | None = None

    async def get(self) -> Stats | None:
        """Las últimas estadísticas compartidas válidas (o None si nunca llegaron)."""
        if not self._loaded:  # al empezar: la copia local, y su fecha es la de la descarga
            self._loaded = True
            self._stats = await asyncio.to_thread(read_cache, self._cache)
            if self._stats is not None:
                with contextlib.suppress(OSError):
                    self._checked = self._cache.stat().st_mtime
        if self._now() - self._checked >= self._refresh:
            self._checked = self._now()
            await self._download()
        return self._stats

    async def _download(self) -> None:
        try:
            data = await download_pack(self._url)
            stats = decode_pack(data)
        except PackError as exc:
            log.warning("Estadísticas compartidas no válidas o no disponibles: %s", exc)
            return
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            log.info("Sin conexión para las estadísticas compartidas: %s", exc)
            return
        try:
            await asyncio.to_thread(_write_atomic, self._cache, data)
        except OSError as exc:
            log.warning("No se ha podido guardar la copia de las estadísticas: %s", exc)
        self._stats = stats
        log.info("Estadísticas compartidas: parche %s, %d partidas", stats[0], stats[1])


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
