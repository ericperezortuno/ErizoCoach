"""Comandos ``lolcoach stats ...``: clave, descarga y estado de las estadísticas."""

from __future__ import annotations

import asyncio
import getpass
import logging
import time

from ..config import Settings
from ..gamedata.catalog import GameData
from ..lcu.client import LcuClient, LcuError
from ..lcu.connector import fetch_client_info
from ..lcu.discovery import ClientLocator
from ..lcu.tls import create_ssl_context
from ..logs import REDACTOR
from . import keystore
from .crawler import Crawler, CrawlProgress
from .riot_api import RiotApiClient, RiotApiError, RiotApiKeyError
from .service import prepare_stats
from .store import MatchStore
from .updater import download_lock

ui = logging.getLogger("lolcoach.ui")
PROGRESS_EVERY_SECONDS = 30


MAX_KEY_ATTEMPTS = 3


def cmd_key(settings: Settings) -> int:
    ui.info("Pega tu clave de la API de Riot (developer.riotgames.com). No se verá al escribir.")
    for attempt in range(1, MAX_KEY_ATTEMPTS + 1):
        try:
            key = getpass.getpass("Clave: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
        if not key:
            continue
        if not keystore.is_valid_format(key):
            ui.error(
                "Eso no parece una clave de Riot: tiene que ser RGAPI- seguido de 36 caracteres "
                "(se recibieron %d caracteres en total). Prueba a copiarla de nuevo.",
                len(key),
            )
            continue
        REDACTOR.add_secret(key)
        try:
            asyncio.run(_check_key(key, settings.riot_region))
        except RiotApiKeyError as exc:
            ui.error("Riot rechaza la clave (HTTP %s: %s).", exc.status, exc.riot_message or "?")
            ui.info(_key_hint(exc))
            if attempt < MAX_KEY_ATTEMPTS:
                ui.info("Pega la clave nueva para volver a intentarlo (o Ctrl+C para salir).")
            continue
        except RiotApiError as exc:
            ui.error("No se pudo comprobar la clave con Riot: %s", exc)
            return 1
        try:
            keystore.save_key(key)
        except keystore.KeyStoreError as exc:
            ui.error("No se pudo guardar la clave: %s", exc)
            return 1
        ui.info("Clave comprobada y guardada en el Administrador de credenciales de Windows.")
        ui.info("LoL Coach la usará en su próxima descarga automática (no hace falta nada más).")
        return 0
    ui.error("No se ha guardado ninguna clave.")
    return 1


def _key_hint(exc: RiotApiKeyError) -> str:
    message = exc.riot_message.lower()
    if "unknown apikey" in message:
        return (
            "Riot no reconoce esa clave. Casi siempre es porque ha caducado (duran 24 h) o es "
            "una antigua: en developer.riotgames.com pulsa «REGENERATE API KEY», recarga la "
            "página y copia la clave nueva con el botón de copiar que hay a su lado. Para no "
            "renovarla cada día, pide una clave personal (docs/clave-personal-riot.md)."
        )
    if exc.status == 403:
        return (
            "Riot reconoce la clave pero no le da permiso. Si acabas de crearla, revisa que tu "
            "cuenta de desarrollador esté verificada (correo) y genera una nueva."
        )
    return "Genera una clave nueva en developer.riotgames.com y vuelve a intentarlo."


async def _check_key(key: str, region: str) -> None:
    async with RiotApiClient(key, region) as api:
        await api.platform_status()


def cmd_forget_key() -> int:
    if keystore.delete_key():
        ui.info("Clave de Riot eliminada del Administrador de credenciales.")
    else:
        ui.info("No había ninguna clave guardada.")
    return 0


def cmd_update(settings: Settings, hours: float, max_matches: int | None) -> int:
    key = keystore.load_key()
    if not key:
        ui.error("No hay clave de Riot guardada. Primero: «uv run lolcoach stats clave».")
        return 1
    lock = download_lock(settings.stats_dir)
    if not lock.acquire():
        ui.error(
            "LoL Coach ya está descargando estadísticas en segundo plano (se pausa solo al "
            "jugar). No hace falta lanzarlo a mano; si aun así quieres, cierra LoL Coach antes."
        )
        return 1
    try:
        return asyncio.run(_update(settings, key, hours, max_matches))
    except KeyboardInterrupt:
        ui.info("Descarga detenida. Lo descargado está guardado; puedes continuar cuando quieras.")
        return 0
    finally:
        lock.release()


async def _update(settings: Settings, key: str, hours: float, max_matches: int | None) -> int:
    patch, gamedata = await _client_patch_and_data(settings)
    ui.info(
        "Descargando partidas de alto elo (%s)%s durante %s... Ctrl+C para parar (se guarda todo).",
        settings.riot_region.upper(),
        f" del parche {patch}" if patch else "",
        f"hasta {max_matches} partidas o {hours:g} h" if max_matches else f"{hours:g} h",
    )
    store = MatchStore(settings.stats_dir / "matches.sqlite3")
    last_report = [time.monotonic()]

    def report(progress: CrawlProgress) -> None:
        if time.monotonic() - last_report[0] < PROGRESS_EVERY_SECONDS:
            return
        last_report[0] = time.monotonic()
        completed = f" · {progress.completed} completadas con runas" if progress.completed else ""
        ui.info(
            "  %d partidas nuevas del parche %s%s · %d jugadores · %d peticiones · %d min",
            progress.new_matches, progress.patch or "?", completed, progress.players,
            progress.requests, progress.elapsed // 60,
        )  # fmt: skip

    progress = CrawlProgress()
    try:
        async with RiotApiClient(key, settings.riot_region) as api:
            crawler = Crawler(
                api, store, patch=patch, max_matches=max_matches,
                max_seconds=hours * 3600, on_progress=report,
            )  # fmt: skip
            try:
                progress = await crawler.run()
            except RiotApiKeyError as exc:
                ui.error("%s", exc)
                progress = crawler.progress
                progress.stopped_reason = "clave caducada"
            except RiotApiError as exc:
                ui.error("Sin conexión con Riot: %s. Lo descargado está guardado.", exc)
                progress = crawler.progress
                progress.stopped_reason = "sin conexión"
        total = store.match_count(progress.patch) if progress.patch else 0
    finally:
        store.close()
    ui.info(
        "Fin (%s): %d partidas nuevas; total del parche %s: %d partidas.",
        progress.stopped_reason or "completado", progress.new_matches,
        progress.patch or "?", total,
    )  # fmt: skip
    if gamedata is not None and progress.patch:
        result = await asyncio.to_thread(
            prepare_stats, settings.stats_dir, progress.patch, gamedata
        )
        if result is not None:
            covered = sum(e.games >= settings.stats_min_games for e in result[2].values())
            ui.info("Builds listas: %d campeones/posiciones con datos suficientes.", covered)
    return 0


async def _client_patch_and_data(settings: Settings) -> tuple[str | None, GameData | None]:
    """El parche (y los objetos) del cliente, si está abierto; si no, se deduce de las partidas."""
    credentials = await asyncio.to_thread(ClientLocator(settings.league_dir).locate)
    if credentials is None:
        return None, None
    REDACTOR.add_secret(*credentials.secrets)
    try:
        async with LcuClient(credentials, create_ssl_context()) as client:
            info = await fetch_client_info(client)
            gamedata = GameData()
            await gamedata.load(client, info.version)
            return info.patch, gamedata
    except LcuError:
        return None, None


def cmd_status(settings: Settings) -> int:
    ui.info("Clave de Riot guardada: %s", "sí" if keystore.load_key() else "no")
    database = settings.stats_dir / "matches.sqlite3"
    if not database.exists():
        ui.info("Aún no hay partidas descargadas («uv run lolcoach stats descargar»).")
        return 0
    store = MatchStore(database)
    try:
        ui.info("Jugadores de alto elo conocidos: %d", store.player_count())
        for patch, count in store.patches()[:3]:
            ui.info(
                "Parche %s: %d partidas (unas %d builds de jugadores), %d con runas y hechizos",
                patch, count, count * 10, store.loadout_count(patch),
            )  # fmt: skip
    finally:
        store.close()
    return 0
