"""Descarga partidas clasificatorias de alto elo del parche actual.

Recorrido, en cada región (EUW, Corea y Norteamérica a la vez: Riot cuenta los
límites de la clave por separado en cada una, así que se descarga el triple):

1. Ligas Challenger, Gran Maestro y Maestro, y Diamante I y II (por páginas)
   -> jugadores. Se consultan antes los de Maestro o más.
2. Partidas del parche guardadas sin runas ni hechizos (versión 0.5.0): se
   vuelve a pedir su detalle (1 petición) para completarlas.
3. Para cada jugador, sus últimas partidas clasificatorias en solitario.
4. Para cada partida nueva del parche actual: detalle + línea temporal
   (2 peticiones) -> builds, runas y hechizos de sus 10 jugadores, guardados al
   momento.

Las partidas ya descargadas no se vuelven a pedir, y al llegar a partidas de un
parche anterior se pasa al siguiente jugador. Con una clave personal (100
peticiones cada 2 minutos) salen unas 1.500 partidas por hora en cada región.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .extract import attach_loadouts, extract_builds, match_patch, patch_key
from .riot_api import (
    RiotApiClient,
    RiotApiError,
    RiotApiKeyError,
    RiotApiStaleIdError,
    match_prefix,
)
from .store import MatchStore, players_refreshed_key

log = logging.getLogger(__name__)

APEX_TIERS = ("CHALLENGER", "GRANDMASTER", "MASTER")
DIVISIONS = ("DIAMOND I", "DIAMOND II")  # hasta Diamante II incluido
TIERS = (*APEX_TIERS, *DIVISIONS)
MAX_LEAGUE_PAGES = 80  # unos 200 jugadores por página: de sobra para cualquier división
PLAYERS_REFRESH_SECONDS = 24 * 3600
MATCHES_PER_PLAYER = 20
NO_PLAYERS_LEFT = "no quedan jugadores por consultar"


@dataclass
class MatchBudget:
    """Partidas nuevas que aún se quieren, compartidas por las regiones que descargan a la vez."""

    left: int
    total: int = field(init=False)

    def __post_init__(self) -> None:
        self.total = self.left

    @property
    def spent(self) -> bool:
        return self.left <= 0


@dataclass
class CrawlProgress:
    region: str = ""
    patch: str = ""
    new_matches: int = 0  # partidas del parche guardadas en esta sesión
    completed: int = 0  # partidas antiguas completadas con runas y hechizos
    skipped: int = 0  # de otros parches o demasiado cortas
    players: int = 0
    requests: int = 0
    started: float = field(default_factory=time.monotonic)
    stopped_reason: str = ""

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


def is_temporary(exc: RiotApiError) -> bool:
    """Errores que se arreglan solos (sin conexión, demasiadas peticiones, Riot caído)."""
    return exc.status == 0 or exc.status == 429 or exc.status >= 500


class Crawler:
    def __init__(
        self,
        api: RiotApiClient,
        store: MatchStore,
        *,
        patch: str | None = None,
        max_matches: int | None = None,
        budget: MatchBudget | None = None,
        max_seconds: float | None = None,
        tiers: tuple[str, ...] = TIERS,
        on_progress: Callable[[CrawlProgress], None] | None = None,
    ) -> None:
        self._api = api
        self._region = api.region
        self._store = store
        self._patch = patch
        # El límite de partidas nuevas: el compartido con otras regiones o uno propio
        self._budget = budget or (MatchBudget(max_matches) if max_matches is not None else None)
        self._max_seconds = max_seconds
        self._tiers = tiers
        self._on_progress = on_progress or (lambda _p: None)
        self.progress = CrawlProgress(region=self._region)
        self._leagues_cut_short = False

    async def run(self) -> CrawlProgress:
        await self._refresh_players()
        await self._complete_loadouts()
        try:
            await self._crawl_players()
        except RiotApiStaleIdError:
            # Jugadores guardados con otra clave: sus PUUID ya no valen. Se releen las ligas
            # (los identificadores llegan cifrados para la clave actual) y se sigue.
            forgotten = self._store.forget_players(self._region)
            log.info(
                "Los %d jugadores guardados de %s eran de otra clave de Riot: se vuelven a "
                "leer las ligas", forgotten, self._region,
            )  # fmt: skip
            await self._refresh_players()
            await self._crawl_players()
        return self.progress

    async def _crawl_players(self) -> None:
        for player_id in self._store.players_to_crawl(self._region):
            if self._should_stop():
                return
            await self._crawl_player(player_id)
            self.progress.players += 1
            self.progress.requests = self._api.requests
            self._on_progress(self.progress)
        self.progress.stopped_reason = NO_PLAYERS_LEFT

    def _should_stop(self) -> bool:
        if self._budget is not None and self._budget.spent:
            self.progress.stopped_reason = f"alcanzadas {self._budget.total} partidas"
            return True
        return self._out_of_time()

    def _out_of_time(self) -> bool:
        if self._max_seconds is not None and self.progress.elapsed >= self._max_seconds:
            self.progress.stopped_reason = "tiempo agotado"
            return True
        return False

    async def _complete_loadouts(self) -> None:
        """Añade runas y hechizos a las partidas que se guardaron sin ellos."""
        patch = self._patch or max((p for p, _ in self._store.patches()), key=patch_key, default="")
        if not patch:
            return
        self.progress.patch = patch
        prefix = match_prefix(self._region)  # cada región completa las suyas
        while pending := self._store.matches_without_loadout(patch, prefix=prefix):
            for match_id in pending:
                if self._out_of_time():  # el límite de partidas es para las nuevas
                    return
                try:
                    match = await self._api.match(match_id)
                except RiotApiKeyError:
                    raise
                except RiotApiError as exc:
                    if is_temporary(exc):
                        raise  # sin conexión o Riot saturado: se reintenta más tarde
                    log.debug("No se pudo completar la partida %s: %s", match_id, exc)
                    self._store.complete_loadout(match_id, None)  # ya no existe: se deja así
                    continue
                builds = attach_loadouts(self._store.match_builds(match_id), match)
                self._store.complete_loadout(match_id, builds)
                self.progress.completed += 1
                self.progress.requests = self._api.requests
                self._on_progress(self.progress)

    async def _refresh_players(self) -> None:
        meta = players_refreshed_key(self._region)
        last = float(self._store.get_meta(meta) or 0)
        if self._store.player_count(self._region) and time.time() - last < PLAYERS_REFRESH_SECONDS:
            return
        players: list[tuple[str, str]] = []
        for tier in self._tiers:
            try:
                entries = await self._league_entries(tier)
            except RiotApiKeyError:
                raise
            except RiotApiError as exc:
                log.warning("No se pudo leer la liga %s de %s: %s", tier, self._region, exc)
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("puuid"):
                    players.append((str(entry["puuid"]), tier))
                elif entry.get("summonerId"):
                    players.append((f"sid:{entry['summonerId']}", tier))
        added = self._store.add_players(players, self._region)
        if not self._leagues_cut_short:
            self._store.set_meta(meta, str(time.time()))
        log.info(
            "Jugadores de alto elo de %s: %d (%d nuevos)",
            self._region, self._store.player_count(self._region), added,
        )  # fmt: skip

    async def _league_entries(self, tier: str) -> list[object]:
        """Los jugadores de una liga: entera (Maestro o más) o por páginas (Diamante I...)."""
        if tier in APEX_TIERS:
            league = await self._api.league(tier)
            entries = league.get("entries", []) if isinstance(league, dict) else []
            return list(entries) if isinstance(entries, list) else []
        name, _, division = tier.partition(" ")
        found: list[object] = []
        for page in range(1, MAX_LEAGUE_PAGES + 1):
            if self._out_of_time():
                self._leagues_cut_short = True  # la siguiente tanda las vuelve a leer
                break
            entries = await self._api.league_entries(name, division, page)
            if not entries:
                break  # última página
            found.extend(entries)
        return found

    async def _crawl_player(self, player_id: str) -> None:
        puuid = player_id
        try:
            if player_id.startswith("sid:"):
                puuid = await self._api.summoner_puuid(player_id[4:])
                if not puuid:
                    self._store.mark_crawled(player_id)
                    return
            match_ids = await self._api.match_ids(puuid, count=MATCHES_PER_PLAYER)
        except (RiotApiKeyError, RiotApiStaleIdError):
            raise
        except RiotApiError as exc:
            log.debug("Jugador omitido: %s", exc)
            self._store.mark_crawled(player_id)
            return

        for match_id in match_ids:
            if self._should_stop():
                break
            if self._store.has_match(match_id):
                continue
            if not await self._crawl_match(match_id):
                break  # partidas de un parche anterior: las siguientes son aún más antiguas
        self._store.mark_crawled(player_id, puuid)

    async def _crawl_match(self, match_id: str) -> bool:
        """Devuelve False si la partida es de un parche anterior al que se busca."""
        try:
            match = await self._api.match(match_id)
        except RiotApiKeyError:
            raise
        except RiotApiError as exc:
            log.debug("Partida %s omitida: %s", match_id, exc)
            self._store.add_match(match_id, "", None)
            return True

        patch = match_patch(match)
        if not self._patch:
            self._patch = patch  # sin cliente abierto: el parche de la partida más reciente
        self.progress.patch = self._patch
        if patch != self._patch:
            self._store.add_match(match_id, patch, None)
            self.progress.skipped += 1
            return patch_key(patch) > patch_key(self._patch)

        try:
            timeline = await self._api.timeline(match_id)
        except RiotApiKeyError:
            raise
        except RiotApiError as exc:
            log.debug("Línea temporal de %s no disponible: %s", match_id, exc)
            timeline = None
        builds = extract_builds(match, timeline) if timeline is not None else []
        self._store.add_match(match_id, patch, builds or None)
        if builds:
            self.progress.new_matches += 1
            if self._budget is not None:
                self._budget.left -= 1
        else:
            self.progress.skipped += 1
        return True


@dataclass
class RegionsResult:
    """Lo que ha hecho cada región: su progreso y, si se cortó, por qué."""

    progress: list[CrawlProgress]
    errors: dict[str, RiotApiError]

    @property
    def new_matches(self) -> int:
        return sum(p.new_matches for p in self.progress)

    @property
    def advanced(self) -> bool:
        return any(p.new_matches or p.completed or p.players for p in self.progress)

    @property
    def patch(self) -> str:
        return next((p.patch for p in self.progress if p.patch), "")

    @property
    def out_of_players(self) -> bool:
        """Todas las regiones han consultado ya a todos sus jugadores."""
        return all(p.stopped_reason == NO_PLAYERS_LEFT for p in self.progress)


async def crawl_regions(
    apis: Sequence[RiotApiClient],
    store: MatchStore,
    *,
    patch: str | None = None,
    max_matches: int | None = None,
    max_seconds: float | None = None,
    tiers: tuple[str, ...] = TIERS,
    on_progress: Callable[[CrawlProgress], None] | None = None,
) -> RegionsResult:
    """Descarga de varias regiones a la vez, con un único tope de partidas para todas.

    Una región sin conexión o con Riot caído no para a las demás (queda en ``errors``); una
    clave rechazada sí lo para todo (``RiotApiKeyError``).
    """
    budget = MatchBudget(max_matches) if max_matches is not None else None
    crawlers = [
        Crawler(
            api,
            store,
            patch=patch,
            budget=budget,
            max_seconds=max_seconds,
            tiers=tiers,
            on_progress=on_progress,
        )
        for api in apis
    ]
    outcomes = await asyncio.gather(*(c.run() for c in crawlers), return_exceptions=True)
    errors: dict[str, RiotApiError] = {}
    for crawler, outcome in zip(crawlers, outcomes, strict=True):
        if isinstance(outcome, RiotApiKeyError):
            raise outcome
        if isinstance(outcome, RiotApiError):
            errors[crawler.progress.region] = outcome
            crawler.progress.stopped_reason = f"cortada: {outcome}"
        elif isinstance(outcome, BaseException):
            raise outcome
    return RegionsResult([c.progress for c in crawlers], errors)
