"""Descargas de la actualización: solo de GitHub, con límites y comprobación de hash.

Lo que llega por red no es de fiar hasta que se comprueba, así que:

* Solo ``https`` y solo los dominios de :data:`ALLOWED_HOSTS` (``github.com`` y los
  subdominios de ``githubusercontent.com``, a donde GitHub redirige los archivos de una
  release). Un parecido no vale: ``githubusercontent.com.evil.io`` o ``evilgithub.com`` se
  rechazan, y también las direcciones con usuario (``https://github.com@evil.io``).
* Las redirecciones se siguen a mano (como mucho :data:`MAX_REDIRECTS`) y el dominio se
  comprueba en cada salto: una redirección a otro sitio se rechaza sin seguirla.
* Cada descarga tiene un tope de bytes. El instalador debe medir exactamente lo que dice el
  manifiesto y tener su SHA-256; se baja a un archivo temporal ``.part`` y solo se renombra
  al nombre final cuando todo cuadra. Si algo falla (error HTTP, de más, de menos, hash
  distinto, conexión cortada, tiempo agotado) no queda ningún archivo.
* Todo fallo sale como :class:`UpdateError`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from .manifest import Manifest, UpdateError

log = logging.getLogger(__name__)

RELEASES = "https://github.com/eriizzo/ErizoCoach/releases"
MANIFEST_URL = f"{RELEASES}/latest/download/erizocoach-version.json"
SIGNATURE_URL = MANIFEST_URL + ".sig"

# Un punto delante significa «cualquier subdominio» (pero no el dominio a secas).
ALLOWED_HOSTS = ("github.com", ".githubusercontent.com")
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
CHUNK = 65_536
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)  # manifiesto y firma: archivos diminutos
INSTALLER_TIMEOUT = aiohttp.ClientTimeout(total=15 * 60, sock_connect=15)  # el instalador
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def installer_url(m: Manifest) -> str:
    return f"{RELEASES}/download/v{m.version}/{m.installer}"


def allowed_url(
    url: str,
    hosts: tuple[str, ...] = ALLOWED_HOSTS,
    *,
    schemes: tuple[str, ...] = ("https",),
) -> bool:
    """¿Es una dirección a la que se puede ir? Esquema, sin usuario y dominio de la lista."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port  # lanza ValueError si el puerto no es válido
    except ValueError:
        return False
    if parts.scheme not in schemes or not host:
        return False
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False
    host = host.lower()
    for entry in hosts:
        entry = entry.lower()
        if entry.startswith("."):
            if host.endswith(entry):
                return True
        elif host == entry:
            return True
    return False


def _discard(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


class Downloader:
    def __init__(
        self,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
        hosts: tuple[str, ...] = ALLOWED_HOSTS,
        schemes: tuple[str, ...] = ("https",),
    ) -> None:
        self._session_factory = session_factory
        self._hosts = hosts
        self._schemes = schemes

    async def fetch(self, url: str, limit: int) -> bytes | None:
        """Descarga hasta ``limit`` bytes. ``None`` si el servidor dice 404."""
        try:
            async with self._get(url, FETCH_TIMEOUT) as response:
                if response.status == 404:
                    return None
                self._check_status(response)
                if (response.content_length or 0) > limit:
                    raise UpdateError("la respuesta es demasiado grande")
                data = bytearray()
                async for chunk in response.content.iter_chunked(CHUNK):
                    data += chunk
                    if len(data) > limit:
                        raise UpdateError("la respuesta es demasiado grande")
                return bytes(data)
        except UpdateError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise UpdateError(f"no se ha podido descargar ({exc})") from exc

    async def to_file(
        self,
        url: str,
        path: Path,
        size: int,
        sha256: str,
        progress: Callable[[int], Awaitable[None]],
    ) -> None:
        """Descarga el instalador a ``path``: exactamente ``size`` bytes con ese SHA-256.

        ``progress`` recibe el porcentaje (0 a 100) cada vez que cambia el entero.
        """
        if size <= 0 or not _SHA256.fullmatch(sha256):
            raise UpdateError("tamaño o hash no válidos")
        part = path.with_suffix(path.suffix + ".part")
        try:
            await self._download(url, part, size, sha256.lower(), progress)
            part.replace(path)
        except BaseException as exc:
            _discard(part, path)
            if isinstance(exc, (aiohttp.ClientError, TimeoutError, OSError)):
                raise UpdateError(f"no se ha podido descargar ({exc})") from exc
            raise

    async def _download(
        self,
        url: str,
        part: Path,
        size: int,
        sha256: str,
        progress: Callable[[int], Awaitable[None]],
    ) -> None:
        digest = hashlib.sha256()
        received = 0
        last = -1
        async with self._get(url, INSTALLER_TIMEOUT) as response:
            self._check_status(response)
            if response.content_length not in (None, size):
                raise UpdateError("el tamaño del instalador no es el esperado")
            part.parent.mkdir(parents=True, exist_ok=True)
            with part.open("wb") as out:
                async for chunk in response.content.iter_chunked(CHUNK):
                    received += len(chunk)
                    if received > size:
                        raise UpdateError("el instalador es más grande de lo esperado")
                    digest.update(chunk)
                    out.write(chunk)
                    percent = min(received * 100 // size, 99)
                    if percent != last:
                        last = percent
                        await progress(percent)
        if received != size:
            raise UpdateError("el instalador está incompleto")
        if digest.hexdigest() != sha256:
            raise UpdateError("el instalador no coincide con su huella SHA-256")
        await progress(100)

    @staticmethod
    def _check_status(response: aiohttp.ClientResponse) -> None:
        if response.status != 200:
            raise UpdateError(f"el servidor responde HTTP {response.status}")

    @asynccontextmanager
    async def _get(
        self, url: str, client_timeout: aiohttp.ClientTimeout
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """GET con las redirecciones a mano, comprobando el dominio en cada salto."""
        async with self._session_factory(timeout=client_timeout) as session:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                if not allowed_url(current, self._hosts, schemes=self._schemes):
                    raise UpdateError("dirección no permitida")
                request: Any = session.get(
                    current, allow_redirects=False, headers={"Accept-Encoding": "identity"}
                )
                async with request as response:
                    location = response.headers.get("Location")
                    if response.status in REDIRECT_STATUSES and location:
                        current = urljoin(current, location)
                        continue
                    yield response
                    return
            raise UpdateError("demasiadas redirecciones")
