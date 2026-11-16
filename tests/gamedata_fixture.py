"""Datos reales del juego (parche 16.22) grabados del cliente, para las pruebas."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from erizocoach.gamedata.catalog import GameData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gamedata.json"
_RECOMMENDED = re.compile(
    r"^/lol-perks/v1/recommended-pages/champion/(\d+)/position/(\w+)/map/(\d+)$"
)
_CHAMPION = re.compile(r"^/lol-game-data/assets/v1/champions/(\d+)\.json$")


@cache
def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def loaded_gamedata() -> GameData:
    data = GameData()
    data.load_static(fixture(), fixture()["version"])
    return data


def recommended_pages(champion_id: int, position: str, map_id: int) -> list[Any]:
    """Como el cliente: si no hay datos para esa posición, devuelve los de su posición habitual."""
    pages = fixture()["recommendedPages"]
    exact = pages.get(f"{champion_id}/{position}/{map_id}")
    if exact is not None:
        return exact
    for key, value in pages.items():
        champ, _, map_key = key.split("/")
        if int(champ) == champion_id and int(map_key) == map_id:
            return value
    return []


def static_route(path: str) -> Any | None:
    """Respuesta del cliente real para las rutas de datos del juego (None = no es de datos)."""
    data = fixture()
    routes = {
        "/lol-game-data/assets/v1/champion-summary.json": data["championSummary"],
        "/lol-game-data/assets/v1/perks.json": data["perks"],
        "/lol-game-data/assets/v1/perkstyles.json": data["perkstyles"],
        "/lol-game-data/assets/v1/summoner-spells.json": data["summonerSpells"],
        "/lol-perks/v1/recommended-champion-positions": data["recommendedPositions"],
        "/lol-game-data/assets/v1/items.json": data["items"],
    }
    if path in routes:
        return routes[path]
    if match := _RECOMMENDED.match(path):
        return recommended_pages(int(match[1]), match[2], int(match[3]))
    if match := _CHAMPION.match(path):
        return data["champions"].get(match[1])
    return None


class FixtureClient:
    """Imita ``LcuClient.get`` con los datos grabados (solo lectura)."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def get(self, path: str) -> Any:
        self.requests.append(path)
        return static_route(path)
