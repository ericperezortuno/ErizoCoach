# ruff: noqa: E501, ASYNC220, ASYNC221, ASYNC240
# (herramienta de desarrollo de un solo uso: los datos de las escenas se leen mejor en una
#  línea, y abrir Edge o mirar archivos sin asyncio aquí no bloquea nada que importe)
"""Capturas y animaciones del README, hechas con la ventana REAL de Erizo Coach.

Monta el servidor de la ventana (el mismo presentador, ``app.js`` y ``style.css`` que usa la
app), lo alimenta con partidas de ejemplo calculadas por el propio código de Erizo Coach y
lo graba con Microsoft Edge sin ventana (protocolo DevTools):

* Datos del juego: los del parche 16.22 de las pruebas, con las rutas de los iconos.
* Iconos: CommunityDragon (copia pública de los archivos del cliente: las mismas imágenes).
* Builds, runas y hechizos: el paquete público de estadísticas de alto elo (ErizoCoach-datos).

No necesita el cliente de LoL ni toca tus datos: no escribe nada fuera de ``docs/img`` y
de la caché en ``build/capturas``.

Uso:  uv run python scripts/capturas_readme.py
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.factories import AHRI, DraftScenario  # noqa: E402
from tests.gamedata_fixture import FixtureClient, fixture  # noqa: E402
from tests.live_factories import ME_RIOT_ID, all_game_data, live_player  # noqa: E402

from erizocoach.builds.recommender import BuildRecommender  # noqa: E402
from erizocoach.bus import EventBus  # noqa: E402
from erizocoach.champselect.parser import parse_session  # noqa: E402
from erizocoach.events import (  # noqa: E402
    BuildReady,
    ChampSelectStarted,
    GameflowPhaseChanged,
    LiveGameUpdated,
    LoadoutInClient,
    LoadoutPreview,
    RunesBy,
    RunesEditRequested,
    SpellsEditRequested,
)
from erizocoach.gamedata.catalog import GameData  # noqa: E402
from erizocoach.ingame.advisor import advise  # noqa: E402
from erizocoach.ingame.analysis import analyze, item_value  # noqa: E402
from erizocoach.ingame.gold import GoldTracker  # noqa: E402
from erizocoach.ingame.models import parse_all_game_data  # noqa: E402
from erizocoach.loadout.recommender import LoadoutRecommender  # noqa: E402
from erizocoach.models import GameflowPhase, Position, QueueInfo  # noqa: E402
from erizocoach.stats.pack import decode_pack  # noqa: E402
from erizocoach.stats.source import StatsBuildSource  # noqa: E402
from erizocoach.ui.web.presenter import UiPresenter  # noqa: E402
from erizocoach.ui.web.server import UiServer  # noqa: E402

OUT = ROOT / "docs" / "img"
CACHE = ROOT / "build" / "capturas"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
CDRAGON = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/"
PACK_URL = (
    "https://raw.githubusercontent.com/ericperezortuno/ErizoCoach-datos/main/estadisticas.json.gz"
)
ASSETS = "/lol-game-data/assets/"
SR = QueueInfo(420, "Clasificatoria en solitario", "CLASSIC", 11)
WIDTH, HEIGHT = 1056, 700  # el tamaño con el que se abre la ventana de Erizo Coach
ENEMIES = [122, 104, 238, 145, 16]  # Darius, Graves, Zed, Kai'Sa, Soraka


# --- Datos e iconos ---------------------------------------------------------------------------


class Downloads:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        CACHE.mkdir(parents=True, exist_ok=True)

    async def fetch(self, url: str) -> bytes:
        name = url.split("://", 1)[1].replace("/", "_")[-150:]
        cached = CACHE / name
        if cached.exists():
            return cached.read_bytes()
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            response.raise_for_status()
            body = await response.read()
        cached.write_bytes(body)
        return body


class IconClient:
    """Lo que la ventana pide al cliente de LoL (iconos), servido desde CommunityDragon."""

    def __init__(self, downloads: Downloads) -> None:
        self._downloads = downloads

    async def get_bytes(self, path: str) -> bytes:
        if not path.startswith(ASSETS):
            raise ValueError(path)
        return await self._downloads.fetch(CDRAGON + path[len(ASSETS) :].lower())


async def game_data(downloads: Downloads) -> GameData:
    """Los datos del 16.22 de las pruebas, con las rutas reales de los iconos."""
    data = json.loads(json.dumps(fixture()))
    sources = {
        "perks": ("v1/perks.json", lambda raw: raw),
        "summonerSpells": ("v1/summoner-spells.json", lambda raw: raw),
        "items": ("v1/items.json", lambda raw: raw),
    }
    for key, (path, pick) in sources.items():
        icons = {
            e["id"]: e.get("iconPath", "")
            for e in pick(json.loads(await downloads.fetch(CDRAGON + path)))
        }
        for entry in data[key]:
            entry["iconPath"] = icons.get(entry["id"], "")
    styles = json.loads(await downloads.fetch(CDRAGON + "v1/perkstyles.json"))["styles"]
    style_icons = {s["id"]: s.get("iconPath", "") for s in styles}
    for style in data["perkstyles"]["styles"]:
        style["iconPath"] = style_icons.get(style["id"], "")
    gd = GameData()
    gd.load_static(data, data["version"])
    return gd


async def stats_source(downloads: Downloads) -> StatsBuildSource:
    patch, matches, stats = decode_pack(await downloads.fetch(PACK_URL))
    source = StatsBuildSource()
    source.update(patch, matches, stats)
    print(f"Estadísticas: {matches} partidas del parche {patch}, {source.covered} combinaciones")
    return source


# --- Edge sin ventana (protocolo DevTools) ------------------------------------------------------


class Browser:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._ids = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self.frames: list[tuple[float, bytes]] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._process: subprocess.Popen[bytes] | None = None

    async def start(self, port: int = 9333) -> None:
        profile = CACHE / "edge-perfil"
        shutil.rmtree(profile, ignore_errors=True)
        self._process = subprocess.Popen(
            [
                str(EDGE), "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}", "--no-first-run", "--disable-sync",
                "--disable-background-networking", "--disable-extensions",
                "--hide-scrollbars", "--force-color-profile=srgb", "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        for _ in range(100):
            with contextlib.suppress(aiohttp.ClientError, TimeoutError):
                list_url = f"http://127.0.0.1:{port}/json/list"
                async with self._session.get(
                    list_url, timeout=aiohttp.ClientTimeout(total=2)
                ) as response:
                    pages = [t for t in await response.json() if t.get("type") == "page"]
                    if pages:
                        url = pages[0]["webSocketDebuggerUrl"]
                        break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Edge no ha arrancado")
        self._ws = await self._session.ws_connect(url, max_msg_size=0)
        self._reader = asyncio.create_task(self._read())
        await self.send("Page.enable")

    async def stop(self) -> None:
        """Cierra Edge entero: en Windows, cerrar el proceso principal deja vivos a sus hijos."""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.send("Browser.close"), 5)
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        if self._process is not None:
            try:
                self._process.wait(5)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self._process.pid)],
                    check=False, capture_output=True,
                )  # fmt: skip

    async def _read(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            data = json.loads(message.data)
            if "id" in data and data["id"] in self._pending:
                self._pending.pop(data["id"]).set_result(data)
            elif data.get("method") == "Page.screencastFrame":
                params = data["params"]
                if self.frames is not None:
                    self.frames.append((time.monotonic(), base64.b64decode(params["data"])))
                await self._ws.send_json(
                    {"id": self._next(), "method": "Page.screencastFrameAck",
                     "params": {"sessionId": params["sessionId"]}}
                )  # fmt: skip

    def _next(self) -> int:
        self._ids += 1
        return self._ids

    async def send(self, method: str, **params: Any) -> dict[str, Any]:
        assert self._ws is not None
        ident = self._next()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[ident] = future
        await self._ws.send_json({"id": ident, "method": method, "params": params})
        reply = await asyncio.wait_for(future, 30)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply.get("result", {})

    async def size(self, width: int, height: int) -> None:
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width, height=height, deviceScaleFactor=1, mobile=False,
        )  # fmt: skip

    async def js(self, code: str) -> Any:
        result = await self.send(
            "Runtime.evaluate", expression=code, awaitPromise=True, returnByValue=True
        )
        return result.get("result", {}).get("value")

    async def open(self, url: str) -> None:
        await self.send("Page.navigate", url=url)
        await asyncio.sleep(2.5)  # conexión, iconos y animación de entrada

    async def screenshot(self, name: str, *, full: bool = True) -> None:
        if full:  # toda la página, aunque sea más alta que la ventana
            height = int(await self.js("document.documentElement.scrollHeight"))
            width = int(await self.js("window.innerWidth"))
            await self.size(width, max(height, 1))
            await asyncio.sleep(0.6)
        shot = await self.send("Page.captureScreenshot", format="png")
        path = OUT / f"{name}.png"
        image = Image.open(io.BytesIO(base64.b64decode(shot["data"])))
        image.save(path, optimize=True)
        print(
            f"  {path.relative_to(ROOT)}  {image.width}x{image.height}  {path.stat().st_size // 1024} KB"
        )

    async def record_start(self, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.frames = []
        await self.send(
            "Page.startScreencast", format="jpeg", quality=88,
            maxWidth=width, maxHeight=height, everyNthFrame=4,
        )  # fmt: skip

    async def record_stop(self, name: str, *, scale: float = 0.75, quality: int = 70) -> None:
        await self.send("Page.stopScreencast")
        frames, self.frames = self.frames or [], None
        end = time.monotonic()
        images: list[Image.Image] = []
        durations: list[int] = []
        for n, (moment, data) in enumerate(frames):
            following = frames[n + 1][0] if n + 1 < len(frames) else end
            image = Image.open(io.BytesIO(data)).convert("RGB")
            if scale != 1:
                image = image.resize(
                    (round(image.width * scale), round(image.height * scale)), Image.LANCZOS
                )
            images.append(image)
            durations.append(max(round((following - moment) * 1000), 33))
        path = OUT / f"{name}.webp"
        images[0].save(
            path, save_all=True, append_images=images[1:], duration=durations,
            loop=0, quality=quality, method=4,
        )  # fmt: skip
        seconds = sum(durations) / 1000
        print(
            f"  {path.relative_to(ROOT)}  {len(images)} fotogramas, {seconds:.1f} s, {path.stat().st_size // 1024} KB"
        )


# --- Escenas ---------------------------------------------------------------------------------------


def players(minute: float) -> dict[str, Any]:
    """Una partida de ejemplo en el minuto ``minute`` (0 = acaba de empezar)."""
    if minute < 1:
        me = live_player(
            "Ahri", position="MIDDLE", items=(3340,), riot_id=ME_RIOT_ID, level=1, cs=0
        )
        others = [
            live_player(alias, team=team, position=pos, items=(3340,), level=1, cs=0)
            for alias, team, pos in (
                ("Garen", "ORDER", "TOP"), ("LeeSin", "ORDER", "JUNGLE"), ("Jinx", "ORDER", "BOTTOM"),
                ("Thresh", "ORDER", "UTILITY"), ("Darius", "CHAOS", "TOP"), ("Graves", "CHAOS", "JUNGLE"),
                ("Zed", "CHAOS", "MIDDLE"), ("Kaisa", "CHAOS", "BOTTOM"), ("Soraka", "CHAOS", "UTILITY"),
            )
        ]  # fmt: skip
        return all_game_data(me, others, game_time=minute * 60 + 6, gold=500)
    grow = (minute - 14) / 4  # de 14:00 a 18:00 la partida avanza un poco
    me = live_player("Ahri", position="MIDDLE", items=(3020, 6655, 1052, 2003), riot_id=ME_RIOT_ID,
                     level=12, kda=(4, 1, 5), cs=148 + round(grow * 24))  # fmt: skip
    others = [
        live_player("Garen", position="TOP", items=(3047, 3078, 1029), level=11, kda=(3, 0, 2), cs=152),
        live_player("LeeSin", position="JUNGLE", items=(3158, 6692), level=10, kda=(1, 3, 6), cs=110),
        live_player("Jinx", position="BOTTOM", items=(3006, 6672, 1018), level=11, kda=(2, 2, 3), cs=171),
        live_player("Thresh", position="UTILITY", items=(3047, 3866), level=9, kda=(0, 2, 8), cs=28),
        live_player("Darius", team="CHAOS", position="TOP", items=(3047, 1037), level=10,
                    kda=(0, 3, 1), cs=131),
        live_player("Graves", team="CHAOS", position="JUNGLE", items=(3158, 6692, 1037), level=11,
                    kda=(4, 2, 3), cs=126),
        live_player("Zed", team="CHAOS", position="MIDDLE", items=(3158, 6692, 3134), level=12,
                    kda=(3, 3, 2), cs=150),
        live_player("Kaisa", team="CHAOS", position="BOTTOM", items=(3006, 3124, 1043) if grow > 0.5
                    else (3006, 1043), level=11, kda=(3 + (grow > 0.5), 2, 2), cs=160),
        live_player("Soraka", team="CHAOS", position="UTILITY", items=(3158, 3866, 3067), level=9,
                    kda=(0, 1, 6), cs=22),
    ]  # fmt: skip
    gold = 1100 + round(grow * 1900)
    return all_game_data(me, others, game_time=minute * 60, gold=gold)


class Show:
    """Lo que vería la ventana: los mismos eventos que publica la app, en orden."""

    def __init__(self, gd: GameData, source: StatsBuildSource) -> None:
        self.gd, self.source = gd, source
        self.bus = EventBus()
        self.presenter = UiPresenter(self.bus, gd)
        self.gold: GoldTracker | None = None  # oro total de cada jugador, partida a partida
        self.server = UiServer(self.presenter, gd, SimpleNamespace(client=None))  # type: ignore[arg-type]
        self.loadout: Any = None
        self.plan: Any = None
        self.bus.subscribe(RunesEditRequested, self._runes_written)
        self.bus.subscribe(SpellsEditRequested, self._spells_written)

    def use_icons(self, client: IconClient) -> None:
        self.server._icons._connector = SimpleNamespace(client=client)

    async def _runes_written(self, event: RunesEditRequested) -> None:  # como haría el cliente
        await asyncio.sleep(0.25)
        here = self.presenter._in_client
        spells = here.spells if here else self.loadout.spells
        await self.bus.publish(LoadoutInClient(AHRI, event.page, spells, RunesBy.YOU_IN_APP,
                                               RunesBy.PREVIEW, "Erizo Coach: Ahri"))  # fmt: skip

    async def _spells_written(self, event: SpellsEditRequested) -> None:
        await asyncio.sleep(0.25)
        here = self.presenter._in_client
        runes = here.runes if here else self.loadout.runes
        by = here.runes_by if here else RunesBy.PREVIEW
        await self.bus.publish(LoadoutInClient(AHRI, runes, event.spells, by, RunesBy.YOU_IN_APP,
                                               "Erizo Coach: Ahri"))  # fmt: skip

    async def lobby(self) -> None:
        await self.presenter._set(connected=True)
        await self.bus.publish(GameflowPhaseChanged(GameflowPhase.NONE, GameflowPhase.LOBBY))

    async def champ_select(self) -> None:
        start = parse_session(DraftScenario().state(3))
        await self.bus.publish(
            GameflowPhaseChanged(GameflowPhase.LOBBY, GameflowPhase.CHAMP_SELECT)
        )
        await self.bus.publish(ChampSelectStarted(start, SR))

    async def hover(self) -> None:
        snapshot = parse_session(DraftScenario().state(3, hover=AHRI)).with_my_champion(AHRI)
        self.loadout = await LoadoutRecommender(self.gd, stats=self.source).recommend(
            FixtureClient(), snapshot, SR
        )
        self.plan = await BuildRecommender(self.gd, self.source).recommend_for(
            FixtureClient(), AHRI, Position.MIDDLE, ENEMIES, 11
        )
        await self.bus.publish(LoadoutPreview(AHRI, self.loadout))
        await self.bus.publish(BuildReady(self.plan))

    async def applied(self) -> None:
        await self.bus.publish(LoadoutInClient(AHRI, self.loadout.runes, self.loadout.spells,
                                               RunesBy.PREVIEW, RunesBy.PREVIEW, "Erizo Coach: Ahri"))  # fmt: skip

    async def game(self, minute: float) -> None:
        if self.presenter.state["phase"] != GameflowPhase.IN_PROGRESS.value:
            await self.bus.publish(
                GameflowPhaseChanged(GameflowPhase.CHAMP_SELECT, GameflowPhase.IN_PROGRESS)
            )
        game = parse_all_game_data(players(minute))
        gd = self.gd

        def price(item_id: int) -> int:
            return item.price_total if (item := gd.item(item_id)) else 0

        if self.gold is None:
            self.gold = GoldTracker(lambda p: item_value(p, price))
        report = analyze(
            game,
            champion_id=gd.champion_id_by_alias,
            item_price=price,
            profiles=gd._profiles,
            positions_for=gd.positions_for,
            my_position=Position.MIDDLE,
            total_gold=self.gold.update(game),  # como en la app: oro total en directo
        )
        assert game.me is not None
        advice = advise(self.plan, report, game.me.item_ids, game.current_gold, gd.item,
                        gd.champion_name, game_time=game.game_time)  # fmt: skip
        await self.bus.publish(LiveGameUpdated(game, report, advice, self.plan))


CLICK = """(() => {{
  const el = [...document.querySelectorAll({selector!r})].find((el) => {test});
  if (!el) return false;
  el.click();
  return true;
}})()"""


async def click(browser: Browser, selector: str, test: str = "true") -> None:
    if not await browser.js(CLICK.format(selector=selector, test=test)):
        raise RuntimeError(f"No encuentro {selector} ({test})")


async def main() -> None:
    if not EDGE.exists():
        raise SystemExit("Hace falta Microsoft Edge")
    OUT.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        downloads = Downloads(session)
        gd = await game_data(downloads)
        show = Show(gd, await stats_source(downloads))
        show.use_icons(IconClient(downloads))
        await show.server.start()
        browser = Browser(session)
        await browser.start()
        try:
            await browser.size(WIDTH, HEIGHT)
            await show.lobby()
            await browser.open(show.server.url)
            print("Capturas:")

            # 1) Selección de campeones: preseleccionas a Ahri y, a los 5 s, se ponen solas
            await browser.record_start()
            await asyncio.sleep(0.6)
            await show.champ_select()
            await asyncio.sleep(1.4)
            await show.hover()
            await asyncio.sleep(2.6)
            await show.applied()
            await asyncio.sleep(2.0)
            await click(browser, ".rune-choice", "!el.classList.contains('selected')")
            await asyncio.sleep(2.4)
            await browser.record_stop("seleccion")
            await click(
                browser,
                ".rune-choice",
                "el.querySelector('.choice-detail').textContent.startsWith('Recomendada')",
            )
            await asyncio.sleep(1.2)
            await browser.screenshot("runas")
            await browser.size(WIDTH, HEIGHT)

            # 2) Editor de runas y hechizos
            await browser.js("window.scrollTo(0, 0)")
            await browser.record_start()
            await asyncio.sleep(0.5)
            await click(browser, ".edit-toggle")
            await asyncio.sleep(1.2)
            await browser.js(
                "document.querySelector('.rune-editor').scrollIntoView({block: 'center'})"
            )
            await asyncio.sleep(0.8)
            await click(
                browser, ".rune-option.keystone-option", "!el.classList.contains('selected')"
            )
            await asyncio.sleep(1.4)
            await click(browser, ".rune-option.shard-option", "!el.classList.contains('selected')")
            await asyncio.sleep(1.4)
            await browser.js("document.querySelector('.spells').scrollIntoView({block: 'center'})")
            await asyncio.sleep(0.6)
            await click(browser, "button.spell-slot")
            await asyncio.sleep(1.2)
            await click(browser, ".spell-option", "el.getAttribute('aria-label') === 'Teleportar'")
            await asyncio.sleep(1.8)
            await browser.record_stop("editor")

            # 3) Partida: lo inicial al empezar y, en el minuto 14, las líneas
            await browser.record_start()
            await show.game(0)
            await asyncio.sleep(3.0)
            for minute in (14.0, 14.5, 15.0, 15.5, 16.0):
                await show.game(minute)
                await asyncio.sleep(1.5)
            await browser.record_stop("partida")
            await browser.screenshot("partida")
            await browser.size(WIDTH, HEIGHT)
            await show.game(0)
            await asyncio.sleep(1.5)
            await browser.screenshot("inicio", full=False)

            # 4) Ventana estrecha
            await show.game(16)
            await browser.size(400, 820)
            await asyncio.sleep(1.5)
            await browser.js("document.querySelector('.lanes').scrollIntoView()")
            await asyncio.sleep(0.6)
            await browser.screenshot("estrecha", full=False)
        finally:
            await browser.stop()
            await show.server.stop()


if __name__ == "__main__":
    asyncio.run(main())
