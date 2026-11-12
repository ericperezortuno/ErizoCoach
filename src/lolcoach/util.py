"""Conversiones defensivas para leer el JSON del cliente sin romperse.

El cliente de LoL cambia con cada parche: un campo puede faltar, llegar como
``null`` o cambiar de tipo. Estas funciones devuelven siempre un valor válido.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
