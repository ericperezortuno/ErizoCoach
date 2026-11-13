"""Cliente de la API oficial de Riot (https://developer.riotgames.com).

* Solo habla con los servidores oficiales de Riot de tu región (lista cerrada).
* Respeta los límites de peticiones de tu clave (20 por segundo y 100 cada 2
  minutos para claves personales/de desarrollo) y se ajusta a los límites que
  Riot indica en cada respuesta.
* Si Riot pide esperar (HTTP 429), espera lo indicado y reintenta.
* Si la clave caduca o no es válida (401/403), se detiene y lo dice claramente.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import aiohttp

from ..lcu.client import describe_error

log = logging.getLogger(__name__)

# Regiones: servidor de la plataforma (ligas) y enrutamiento regional (partidas)
REGIONS = {
    "euw1": ("https://euw1.api.riotgames.com", "https://europe.api.riotgames.com"),
    "eun1": ("https://eun1.api.riotgames.com", "https://europe.api.riotgames.com"),
    "tr1": ("https://tr1.api.riotgames.com", "https://europe.api.riotgames.com"),
    "na1": ("https://na1.api.riotgames.com", "https://americas.api.riotgames.com"),
    "kr": ("https://kr.api.riotgames.com", "https://asia.api.riotgames.com"),
}
DEFAULT_LIMITS = ((20, 1.0), (100, 120.0))  # claves de desarrollo y personales
TIMEOUT = aiohttp.ClientTimeout(total=15)
MAX_RETRIES = 4


class RiotApiError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class RiotApiKeyError(RiotApiError):
    """La clave no es válida o ha caducado (las de desarrollo duran 24 horas)."""

    def __init__(self, message: str, status: int = 0, riot_message: str = "") -> None:
        super().__init__(message, status)
        self.riot_message = riot_message  # explicación de Riot ("Unknown apikey", "Forbidden"...)


class RateLimiter:
    """Ventanas deslizantes: nunca se supera ninguno de los límites."""

    def __init__(
        self,
        limits: tuple[tuple[int, float], ...] = DEFAULT_LIMITS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self.set_limits(limits)

    @property
    def limits(self) -> tuple[tuple[int, float], ...]:
        return self._limits

    def set_limits(self, limits: tuple[tuple[int, float], ...]) -> None:
        """Cambia los límites sin olvidar las peticiones ya hechas (Riot también las cuenta)."""
        previous: list[deque[float]] = getattr(self, "_windows", [])
        history = max(previous, key=len, default=deque[float]())
        self._limits = tuple(limits)
        self._windows = [deque(history) for _ in self._limits]

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                wait = 0.0
                for (count, period), window in zip(self._limits, self._windows, strict=True):
                    while window and now - window[0] >= period:
                        window.popleft()
                    if len(window) >= count:
                        wait = max(wait, period - (now - window[0]))
                if wait <= 0:
                    for window in self._windows:
                        window.append(now)
                    return
                await self._sleep(wait + 0.05)


def _riot_message(text: str) -> str:
    """Explicación de Riot en una respuesta de error: ``{"status": {"message": ...}}``."""
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    status = data.get("status") if isinstance(data, dict) else None
    message = status.get("message") if isinstance(status, dict) else None
    return str(message)[:200] if message else ""


def parse_limits(header: str | None) -> tuple[tuple[int, float], ...] | None:
    """``"20:1,100:120"`` -> ``((20, 1.0), (100, 120.0))``."""
    if not header:
        return None
    limits: list[tuple[int, float]] = []
    for part in header.split(","):
        count, _, period = part.partition(":")
        try:
            limits.append((int(count), float(period)))
        except ValueError:
            return None
    return tuple(limits) or None


class RiotApiClient:
    def __init__(
        self,
        api_key: str,
        region: str = "euw1",
        *,
        hosts: tuple[str, str] | None = None,
        limiter_factory: Callable[[], RateLimiter] = RateLimiter,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if region not in REGIONS and hosts is None:
            raise ValueError(f"Región no admitida: {region}")
        self._sleep = sleep
        self._api_key = api_key
        self.platform_host, self.regional_host = hosts or REGIONS[region]
        self._allowed = {urlsplit(self.platform_host).netloc, urlsplit(self.regional_host).netloc}
        self._limiters = {host: limiter_factory() for host in self._allowed}
        self._session: aiohttp.ClientSession | None = None
        self.requests = 0

    async def __aenter__(self) -> Self:
        self._session = aiohttp.ClientSession(
            timeout=TIMEOUT, headers={"X-Riot-Token": self._api_key, "Accept": "application/json"}
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

    # --- Endpoints ----------------------------------------------------------------

    async def platform_status(self) -> Any:
        return await self._get(self.platform_host, "/lol/status/v4/platform-data")

    async def league(self, tier: str, queue: str = "RANKED_SOLO_5x5") -> Any:
        paths = {
            "CHALLENGER": "challengerleagues",
            "GRANDMASTER": "grandmasterleagues",
            "MASTER": "masterleagues",
        }
        return await self._get(self.platform_host, f"/lol/league/v4/{paths[tier]}/by-queue/{queue}")

    async def summoner_puuid(self, summoner_id: str) -> str:
        data = await self._get(self.platform_host, f"/lol/summoner/v4/summoners/{summoner_id}")
        return str(data.get("puuid", "")) if isinstance(data, dict) else ""

    async def match_ids(self, puuid: str, *, queue: int = 420, count: int = 20) -> list[str]:
        data = await self._get(
            self.regional_host,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params={"queue": queue, "type": "ranked", "start": 0, "count": count},
        )
        return [m for m in data if isinstance(m, str)] if isinstance(data, list) else []

    async def match(self, match_id: str) -> Any:
        return await self._get(self.regional_host, f"/lol/match/v5/matches/{match_id}")

    async def timeline(self, match_id: str) -> Any:
        return await self._get(self.regional_host, f"/lol/match/v5/matches/{match_id}/timeline")

    # --- Peticiones -----------------------------------------------------------------

    async def _get(self, host: str, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("RiotApiClient no está abierto")
        netloc = urlsplit(host).netloc
        if netloc not in self._allowed:
            raise RiotApiError(f"servidor no permitido: {netloc}")
        limiter = self._limiters[netloc]
        for attempt in range(MAX_RETRIES + 1):
            await limiter.acquire()
            self.requests += 1
            try:
                async with self._session.get(host + path, params=params) as response:
                    status = response.status
                    limits = parse_limits(response.headers.get("X-App-Rate-Limit"))
                    retry_after = response.headers.get("Retry-After")
                    rate_type = response.headers.get("X-Rate-Limit-Type")
                    rate_count = response.headers.get("X-App-Rate-Limit-Count")
                    body = await response.json(content_type=None) if status == 200 else None
                    error_text = "" if status == 200 else _riot_message(await response.text())
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                if attempt == MAX_RETRIES:
                    raise RiotApiError(f"sin respuesta de Riot: {describe_error(exc)}") from exc
                await self._sleep(2**attempt)
                continue
            if limits and limits != limiter.limits:
                log.debug("Límites de la clave según Riot: %s", limits)
                limiter.set_limits(limits)
            if status == 200:
                return body
            if status in (401, 403):
                raise RiotApiKeyError(
                    f"Riot rechaza la clave (HTTP {status}: {error_text or 'sin detalle'}). "
                    "Las claves de desarrollo duran 24 h: genera una nueva en "
                    "developer.riotgames.com y guárdala con «lolcoach stats clave»",
                    status,
                    error_text,
                )
            if status == 404:
                raise RiotApiError("no encontrado", status)
            if status == 429 or status >= 500:
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                log.debug(
                    "Riot pide esperar %.0f s (HTTP %s, tipo %s, cuenta %s)", wait, status,
                    rate_type or "?", rate_count or "?",
                )  # fmt: skip
                await self._sleep(wait)
                continue
            raise RiotApiError(f"HTTP {status}", status)
        raise RiotApiError("demasiados reintentos", 429)
