"""Carga de los datos del juego: un fallo pasajero no deja el catálogo a medias."""

from __future__ import annotations

from typing import Any

from lolcoach.gamedata.catalog import GameData
from lolcoach.lcu import endpoints
from lolcoach.lcu.client import LcuUnavailable

from .gamedata_fixture import static_route


class WarmingUpClient:
    """Como el cliente recién abierto: la lista de objetos falla las primeras veces."""

    def __init__(self, failures: int) -> None:
        self.failures = failures

    async def get(self, path: str) -> Any:
        if path == endpoints.ITEMS and self.failures > 0:
            self.failures -= 1
            raise LcuUnavailable("aún arrancando")
        return static_route(path)


async def test_failed_parts_are_retried_until_everything_loads() -> None:
    gd = GameData()
    await gd.load(WarmingUpClient(failures=2), "16.22.1", retry_delay=0)  # type: ignore[arg-type]
    assert gd.item(3157) is not None  # Zhonya: la lista de objetos llegó al reintentar
    assert gd.version == "16.22.1"


async def test_a_partial_load_is_not_taken_as_complete() -> None:
    gd = GameData()
    client = WarmingUpClient(failures=100)
    await gd.load(client, "16.22.1", retry_delay=0)  # type: ignore[arg-type]
    assert gd.champion_count > 0 and gd.item(3157) is None
    assert gd.version is None  # a medias: la próxima conexión lo vuelve a cargar
    client.failures = 0
    await gd.load(client, "16.22.1", retry_delay=0)  # type: ignore[arg-type]
    assert gd.item(3157) is not None and gd.version == "16.22.1"
