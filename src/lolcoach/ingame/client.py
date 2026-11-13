"""Cliente de la API de partida en vivo (``https://127.0.0.1:2999``).

Igual que con el cliente de LoL, la conexión se verifica con la CA de Riot: solo
se aceptan datos del juego oficial.
"""

from __future__ import annotations

import json
import ssl
from types import TracebackType
from typing import Any, Self

import aiohttp

from ..lcu.client import describe_error

LIVE_CLIENT_URL = "https://127.0.0.1:2999"
ALL_GAME_DATA = "/liveclientdata/allgamedata"
TIMEOUT = aiohttp.ClientTimeout(total=3)


class LiveClientUnavailable(Exception):
    """No hay partida en curso, o aún está cargando."""


class LiveClient:
    def __init__(self, ssl_context: ssl.SSLContext, base_url: str = LIVE_CLIENT_URL) -> None:
        self._ssl_context = ssl_context
        self._base_url = base_url
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> Self:
        self._session = aiohttp.ClientSession(
            base_url=self._base_url,
            connector=aiohttp.TCPConnector(ssl=self._ssl_context, limit=2),
            timeout=TIMEOUT,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session is not None:
            session, self._session = self._session, None
            await session.close()

    async def all_game_data(self) -> Any:
        if self._session is None:
            raise RuntimeError("LiveClient no está abierto")
        try:
            async with self._session.get(ALL_GAME_DATA) as response:
                raw = await response.read()
                status = response.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise LiveClientUnavailable(describe_error(exc)) from exc
        if status != 200:
            raise LiveClientUnavailable(f"HTTP {status}")  # 404 mientras la partida carga
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise LiveClientUnavailable("respuesta no válida") from exc
