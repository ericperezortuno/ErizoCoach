"""Ajustes de la aplicación.

Todo funciona sin configurar nada. Opcionalmente se puede crear
``%LOCALAPPDATA%\\LoLCoach\\config.toml`` con, por ejemplo::

    league_dir = "D:/Riot Games/League of Legends"
    debug = false
    discovery_interval = 3
    auto_runes = true    # poner las runas solas al hacerse el último pick
    auto_spells = true   # poner los hechizos de invocador solos
    auto_item_set = true # poner la build en la tienda (conjunto de objetos)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_DIR_NAME = "LoLCoach"
CONFIG_FILE_NAME = "config.toml"


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / APP_DIR_NAME if base else Path.home() / f".{APP_DIR_NAME.lower()}"


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    league_dir: Path | None = None  # None = detectar automáticamente
    debug: bool = False
    discovery_interval: float = 3.0  # segundos entre intentos de detectar el cliente
    auto_runes: bool = True
    auto_spells: bool = True
    auto_item_set: bool = True

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "lolcoach.lock"

    @property
    def config_path(self) -> Path:
        return self.data_dir / CONFIG_FILE_NAME


def load_settings(
    *,
    data_dir: Path | None = None,
    league_dir: Path | None = None,
    debug: bool | None = None,
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
    )
    return settings, warnings


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
