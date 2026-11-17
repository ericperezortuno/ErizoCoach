"""Ajustes de la aplicación.

Todo funciona sin configurar nada. Opcionalmente se puede crear
``%LOCALAPPDATA%\\ErizoCoach\\config.toml`` con, por ejemplo::

    league_dir = "D:/Riot Games/League of Legends"
    debug = false
    discovery_interval = 3
    auto_runes = true    # poner las runas solas al hacerse el último pick
    auto_spells = true   # poner los hechizos de invocador solos
    auto_item_set = true # poner la build en la tienda (conjunto de objetos)
    window = true              # ventana con consejos (segundo monitor)
    window_on_secondary = true # false = abrirla en el monitor principal
    window_mode = "escritorio" # "escritorio" (ventana propia) o "navegador" (Edge)
    window_on_top = false      # true = ventana siempre encima
    launch_intro = true        # intro de 5 s en el monitor principal al abrir el LoL desde la app
    live_poll_interval = 1     # segundos entre lecturas de la partida (1-10)
    riot_region = "euw1"       # tu región (euw1, eun1, tr1, na1, kr)
    stats_regions = ["euw1", "kr", "na1"] # regiones de las que se descargan partidas
    stats_min_games = 20       # partidas mínimas para usar la build de estadísticas
    stats_auto_download = true # descargar estadísticas solo, con la app abierta
    stats_target_matches = 20000 # partidas por parche a partir de las que se para
    stats_shared = true        # sin clave (o pocos datos): usar las estadísticas compartidas
    update_check = true        # avisar de las versiones nuevas (solo en la app instalada)

Para publicar tus estadísticas para los demás (ver ``stats/publish.py``)::

    stats_publish_repo = "git@github.com:<usuario>/ErizoCoach.git"  # se publica en su rama datos
    stats_publish_key = "C:/Users/<usuario>/.ssh/erizocoach_deploy_ed25519"
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .stats.riot_api import REGIONS, distinct_routes

APP_DIR_NAME = "ErizoCoach"
# Hasta la 0.20 la app se llamaba «LoL Coach» y sus datos estaban en %LOCALAPPDATA%\LoLCoach:
# la primera vez que se abre la versión nueva, esa carpeta pasa a llamarse ErizoCoach.
LEGACY_DIR_NAME = "LoLCoach"
LOCK_FILE_NAME = "erizocoach.lock"
LEGACY_LOCK_FILE_NAME = "lolcoach.lock"  # el de las versiones antiguas, abiertas o no
DEFAULT_TARGET_MATCHES = 20_000
# Una región por servidor de partidas (europe, asia, americas): cada una suma su propio límite
DEFAULT_STATS_REGIONS = ("euw1", "kr", "na1")
CONFIG_FILE_NAME = "config.toml"


def default_data_dir(notes: list[str] | None = None) -> Path:
    """``%LOCALAPPDATA%\\ErizoCoach``; si solo existe la carpeta antigua, se mueve antes."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root, new, old = Path(base), APP_DIR_NAME, LEGACY_DIR_NAME
    else:  # fuera de Windows
        root, new, old = Path.home(), f".{APP_DIR_NAME.lower()}", f".{LEGACY_DIR_NAME.lower()}"
    return migrate_data_dir(root / old, root / new, notes)


def migrate_data_dir(legacy: Path, target: Path, notes: list[str] | None = None) -> Path:
    """Mueve la carpeta de datos del nombre antiguo al nuevo (estadísticas, ajustes, registros).

    Es un cambio de nombre en el mismo disco: no se copia nada ni se puede quedar a medias.
    Si la carpeta antigua está en uso (una versión antigua sigue abierta), se usa tal cual
    y se vuelve a intentar la próxima vez. Si ya existe la nueva, la antigua no se toca.
    """
    if target.exists() or not legacy.is_dir():
        return target
    try:
        legacy.rename(target)
    except OSError as exc:
        _note(notes, f"No se ha podido renombrar {legacy} a {target} ({exc}): se usa la antigua.")
        return legacy
    with contextlib.suppress(OSError):
        (target / LEGACY_LOCK_FILE_NAME).unlink(missing_ok=True)
    _note(notes, f"Tus datos se han movido de {legacy} a {target}.")
    return target


def _note(notes: list[str] | None, text: str) -> None:
    if notes is not None:
        notes.append(text)


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    league_dir: Path | None = None  # None = detectar automáticamente
    debug: bool = False
    discovery_interval: float = 3.0  # segundos entre intentos de detectar el cliente
    auto_runes: bool = True
    auto_spells: bool = True
    auto_item_set: bool = True
    window: bool = True  # ventana para el segundo monitor
    window_on_secondary: bool = True  # False = en el monitor principal
    window_mode: str = "escritorio"  # "escritorio" (ventana propia) o "navegador" (Edge)
    window_on_top: bool = False  # mantener la ventana siempre encima
    launch_intro: bool = True  # intro en el monitor principal al pulsar «Abrir League of Legends»
    live_poll_interval: float = 1.0  # segundos entre lecturas de la partida
    riot_region: str = "euw1"  # tu región (API de Riot)
    # Regiones de las que se descargan partidas a la vez (Riot limita cada una por separado)
    stats_regions: tuple[str, ...] = DEFAULT_STATS_REGIONS
    stats_min_games: int = 20  # partidas mínimas para usar la build de estadísticas
    stats_auto_download: bool = True  # descargar en segundo plano (pausa al jugar)
    stats_target_matches: int = DEFAULT_TARGET_MATCHES  # partidas por parche: luego se para
    stats_shared: bool = True  # usar las estadísticas compartidas si faltan las propias
    stats_publish_repo: str = ""  # publicar las propias (vacío = no se publica)
    stats_publish_key: Path | None = None  # clave SSH con escritura en ese repositorio
    update_check: bool = True  # comprobar si hay una versión nueva (solo en la app instalada)

    @property
    def window_profile_dir(self) -> Path:
        return self.data_dir / "window"

    @property
    def stats_dir(self) -> Path:
        return self.data_dir / "stats"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / LOCK_FILE_NAME

    @property
    def legacy_lock_path(self) -> Path:
        return self.data_dir / LEGACY_LOCK_FILE_NAME


def load_settings(
    *,
    data_dir: Path | None = None,
    league_dir: Path | None = None,
    debug: bool | None = None,
    window_flag: bool | None = None,
) -> tuple[Settings, list[str]]:
    """Combina valores por defecto, ``config.toml`` y opciones de la línea de comandos.

    Devuelve los ajustes y una lista de avisos (valores inválidos que se ignoraron).
    """
    data_dir = data_dir or default_data_dir()
    warnings: list[str] = []
    values = _read_file(data_dir / CONFIG_FILE_NAME, warnings)

    file_league_dir = values.get("league_dir")
    if file_league_dir is not None and not isinstance(file_league_dir, str):
        warnings.append("config.toml: 'league_dir' debe ser texto; se ignora.")
        file_league_dir = None

    file_debug = _bool(values, "debug", False, warnings)
    auto_runes = _bool(values, "auto_runes", True, warnings)
    auto_spells = _bool(values, "auto_spells", True, warnings)
    auto_item_set = _bool(values, "auto_item_set", True, warnings)
    window = _bool(values, "window", True, warnings)
    window_on_secondary = _bool(values, "window_on_secondary", True, warnings)
    window_on_top = _bool(values, "window_on_top", False, warnings)
    launch_intro = _bool(values, "launch_intro", True, warnings)
    window_mode = values.get("window_mode", "escritorio")
    if window_mode not in ("escritorio", "navegador"):
        warnings.append('config.toml: \'window_mode\' debe ser "escritorio" o "navegador".')
        window_mode = "escritorio"
    region = values.get("riot_region", "euw1")
    if region not in REGIONS:
        warnings.append(
            f"config.toml: 'riot_region' debe ser una de {sorted(REGIONS)}; se usa euw1."
        )
        region = "euw1"
    stats_regions = _stats_regions(values, region, warnings)
    min_games = values.get("stats_min_games", 20)
    if isinstance(min_games, bool) or not isinstance(min_games, int) or not 5 <= min_games <= 500:
        warnings.append("config.toml: 'stats_min_games' debe estar entre 5 y 500; se usa 20.")
        min_games = 20
    auto_download = _bool(values, "stats_auto_download", True, warnings)
    shared = _bool(values, "stats_shared", True, warnings)
    update_check = _bool(values, "update_check", True, warnings)
    publish_repo = values.get("stats_publish_repo", "")
    publish_key = values.get("stats_publish_key", "")
    if not isinstance(publish_repo, str) or not isinstance(publish_key, str):
        warnings.append("config.toml: 'stats_publish_repo' y 'stats_publish_key' son texto.")
        publish_repo, publish_key = "", ""
    if bool(publish_repo) != bool(publish_key):
        warnings.append(
            "config.toml: para publicar estadísticas hacen falta 'stats_publish_repo' y "
            "'stats_publish_key'; no se publican."
        )
        publish_repo, publish_key = "", ""
    target = values.get("stats_target_matches", DEFAULT_TARGET_MATCHES)
    if isinstance(target, bool) or not isinstance(target, int) or not 200 <= target <= 100_000:
        warnings.append(
            "config.toml: 'stats_target_matches' debe estar entre 200 y 100000; se usa "
            f"{DEFAULT_TARGET_MATCHES}."
        )
        target = DEFAULT_TARGET_MATCHES
    poll = values.get("live_poll_interval", 1.0)
    if isinstance(poll, bool) or not isinstance(poll, int | float) or not 1 <= poll <= 10:
        warnings.append("config.toml: 'live_poll_interval' debe estar entre 1 y 10; se usa 1.")
        poll = 1.0

    interval = values.get("discovery_interval", 3.0)
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int | float)
        or not 1 <= interval <= 60
    ):
        warnings.append("config.toml: 'discovery_interval' debe estar entre 1 y 60; se usa 3.")
        interval = 3.0

    resolved_league_dir = league_dir or (Path(file_league_dir) if file_league_dir else None)
    settings = Settings(
        data_dir=data_dir,
        league_dir=resolved_league_dir,
        debug=file_debug if debug is None else debug,
        discovery_interval=float(interval),
        auto_runes=auto_runes,
        auto_spells=auto_spells,
        auto_item_set=auto_item_set,
        window=window if window_flag is None else window_flag,
        window_on_secondary=window_on_secondary,
        window_mode=window_mode,
        window_on_top=window_on_top,
        launch_intro=launch_intro,
        live_poll_interval=float(poll),
        riot_region=region,
        stats_regions=stats_regions,
        stats_min_games=min_games,
        stats_auto_download=auto_download,
        stats_target_matches=target,
        stats_shared=shared,
        stats_publish_repo=publish_repo,
        stats_publish_key=Path(publish_key) if publish_key else None,
        update_check=update_check,
    )
    return settings, warnings


def _stats_regions(values: dict[str, Any], home: str, warnings: list[str]) -> tuple[str, ...]:
    """Las regiones de descarga: tu región primero y, si no se dice otra cosa, una de cada
    servidor de partidas. Dos que comparten servidor (euw1 y eun1) se quedan en una."""
    default = (home, *(r for r in DEFAULT_STATS_REGIONS if r != home))
    raw = values.get("stats_regions")
    if raw is None:
        return distinct_routes(default)
    if not isinstance(raw, list) or not raw or not all(r in REGIONS for r in raw):
        warnings.append(
            f"config.toml: 'stats_regions' debe ser una lista de {sorted(REGIONS)}; "
            "se usan las de siempre."
        )
        return distinct_routes(default)
    regions = distinct_routes(tuple(raw))
    if len(regions) < len(set(raw)):
        warnings.append(
            "config.toml: 'stats_regions' tiene regiones que comparten servidor de partidas "
            f"(p. ej., euw1 y eun1); se usan {list(regions)}."
        )
    return regions


def _bool(values: dict[str, Any], key: str, default: bool, warnings: list[str]) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        warnings.append(f"config.toml: '{key}' debe ser true o false; se ignora.")
        return default
    return value


def _read_file(path: Path, warnings: list[str]) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        warnings.append(f"No se pudo leer {path}: {exc}. Se usan los valores por defecto.")
        return {}
