"""Cliente HTTP para la API local del cliente de LoL."""

from __future__ import annotations

import json
import logging
import ssl
from types import TracebackType
from typing import Any, Self

import aiohttp

from .discovery import LcuCredentials

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=5)
SESSION_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=5)


class LcuError(Exception):
    """Error al hablar con el cliente."""


class LcuUnavailable(LcuError):
    """No se puede contactar con el cliente (cerrado, reiniciándose...)."""


class LcuTlsError(LcuUnavailable):
    """El certificado no es de Riot: la conexión se rechaza por seguridad."""


class LcuHttpError(LcuError):
    def __init__(self, method: str, path: str, status: int, message: str = "") -> None:
        detail = f": {message}" if message else ""
        super().__init__(f"{method} {path} -> HTTP {status}{detail}")
        self.method = method
        self.path = path
        self.status = status
        self.message = message


def describe_error(exc: BaseException) -> str:
    """Descripción breve de un error de red.

    Seguridad: nunca usar ``repr()`` con errores de aiohttp, porque incluye las
    cabeceras de la petición (y con ellas la contraseña codificada).
    """
    return f"{type(exc).__name__}: {exc}"


def open_session(credentials: LcuCredentials, ssl_context: ssl.SSLContext) -> aiohttp.ClientSession:
    """Sesión HTTPS autenticada, limitada a 127.0.0.1 y a la CA de Riot."""
    return aiohttp.ClientSession(
        base_url=credentials.base_url,
        connector=aiohttp.TCPConnector(ssl=ssl_context, limit=8),
        headers={"Accept": "application/json", "Authorization": credentials.authorization},
        timeout=SESSION_TIMEOUT,
    )


class LcuClient:
    """Peticiones a la API del cliente. Usar con ``async with``."""

    def __init__(self, credentials: LcuCredentials, ssl_context: ssl.SSLContext) -> None:
        self._credentials = credentials
        self._ssl_context = ssl_context
        self._session: aiohttp.ClientSession | None = None

    @property
    def credentials(self) -> LcuCredentials:
        return self._credentials

    @property
    def ssl_context(self) -> ssl.SSLContext:
        return self._ssl_context

    async def __aenter__(self) -> Self:
        self._session = open_session(self._credentials, self._ssl_context)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            session, self._session = self._session, None
            await session.close()

    async def request(self, method: str, path: str, *, body: Any = None) -> Any:
        if self._session is None:
            raise RuntimeError("LcuClient no está abierto")
        if not path.startswith("/"):
            raise ValueError(f"La ruta debe empezar por '/': {path!r}")
        kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
        if body is not None:
            kwargs["json"] = body
        try:
            async with self._session.request(method, path, **kwargs) as response:
                raw = await response.read()
                status = response.status
        except aiohttp.ClientSSLError as exc:
            raise LcuTlsError(f"certificado no válido: {describe_error(exc)}") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise LcuUnavailable(f"{method} {path}: {describe_error(exc)}") from exc

        data = _decode(raw)
        if status >= 400:
            message = data.get("message", "") if isinstance(data, dict) else ""
            raise LcuHttpError(method, path, status, str(message))
        return data

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

    async def get_bytes(self, path: str) -> bytes:
        """Descarga un recurso binario del cliente (iconos de ``/lol-game-data/assets/``)."""
        if self._session is None:
            raise RuntimeError("LcuClient no está abierto")
        if not path.startswith("/lol-game-data/assets/"):
            raise ValueError(f"Solo se permiten recursos del juego: {path!r}")
        try:
            async with self._session.get(path, timeout=REQUEST_TIMEOUT) as response:
                body = await response.read()
                if response.status >= 400:
                    raise LcuHttpError("GET", path, response.status)
                return body
        except aiohttp.ClientSSLError as exc:
            raise LcuTlsError(f"certificado no válido: {describe_error(exc)}") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise LcuUnavailable(f"GET {path}: {describe_error(exc)}") from exc

    async def post(self, path: str, body: Any = None) -> Any:
        return await self.request("POST", path, body=body)

    async def put(self, path: str, body: Any = None) -> Any:
        return await self.request("PUT", path, body=body)

    async def patch(self, path: str, body: Any = None) -> Any:
        return await self.request("PATCH", path, body=body)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")
