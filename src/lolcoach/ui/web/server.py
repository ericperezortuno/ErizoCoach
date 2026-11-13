"""Servidor local de la ventana.

Seguridad:

* Solo escucha en ``127.0.0.1`` (nada es accesible desde la red).
* La página y el canal en tiempo real exigen una **clave aleatoria** que cambia en
  cada arranque.
* Se comprueba la cabecera ``Host`` (evita ataques de *DNS rebinding*) y el
  ``Origin`` del websocket: una web abierta en tu navegador no puede leer la ventana.
* Cabeceras estrictas (CSP sin recursos externos, sin incrustar en otras webs).
* Los iconos solo se piden por id numérico y solo de los recursos del juego.
"""

from __future__ import annotations

import logging
import secrets
from importlib.resources import files
from typing import Any

from aiohttp import WSMsgType, web

from ...gamedata.catalog import GameData
from ...lcu.connector import LcuConnector
from .icons import IconProvider
from .presenter import State, UiPresenter

log = logging.getLogger(__name__)

STATIC_FILES = {"app.js": "text/javascript", "style.css": "text/css"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class UiServer:
    def __init__(
        self,
        presenter: UiPresenter,
        gamedata: GameData,
        connector: LcuConnector | None,
        *,
        port: int = 0,
    ) -> None:
        self._presenter = presenter
        self._gamedata = gamedata
        self._connector = connector
        self._requested_port = port
        self.token = secrets.token_urlsafe(24)
        self.port = 0
        self._sockets: set[web.WebSocketResponse] = set()
        self._icons = IconProvider(gamedata, connector)
        self._runner: web.AppRunner | None = None
        self._static = files("lolcoach.ui.web").joinpath("static")

        app = web.Application(middlewares=[self._security])
        app.router.add_get("/", self._index)
        app.router.add_get("/static/{name}", self._static_file)
        app.router.add_get("/ws", self._websocket)
        app.router.add_get(r"/icon/item/{item_id:\d{1,6}}.png", self._item_icon)
        app.router.add_get(r"/icon/champion/{champion_id:\d{1,6}}.png", self._champion_icon)
        self._app = app
        presenter.add_listener(self._broadcast)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self._requested_port)
        await site.start()
        self.port = self._runner.addresses[0][1]
        log.debug("Ventana disponible en el puerto %s", self.port)

    async def stop(self) -> None:
        for ws in list(self._sockets):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    # --- Seguridad -----------------------------------------------------------------

    @web.middleware
    async def _security(self, request: web.Request, handler: Any) -> web.StreamResponse:
        allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if request.host not in allowed_hosts:
            return web.Response(status=403, text="Host no permitido")
        response = await handler(request)
        if not isinstance(response, web.WebSocketResponse):
            response.headers.update(SECURITY_HEADERS)
        return response

    def _token_ok(self, request: web.Request) -> bool:
        return secrets.compare_digest(request.query.get("t", ""), self.token)

    # --- Página ----------------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.Response:
        if not self._token_ok(request):
            return web.Response(status=403, text="Clave no válida")
        html = self._static.joinpath("index.html").read_text(encoding="utf-8")
        return web.Response(
            text=html, content_type="text/html", headers={"Cache-Control": "no-store"}
        )

    async def _static_file(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in STATIC_FILES:
            raise web.HTTPNotFound()
        return web.Response(
            text=self._static.joinpath(name).read_text(encoding="utf-8"),
            content_type=STATIC_FILES[name],
            headers={"Cache-Control": "no-store"},
        )

    # --- Tiempo real -----------------------------------------------------------------

    async def _websocket(self, request: web.Request) -> web.StreamResponse:
        expected_origin = f"http://127.0.0.1:{self.port}"
        if not self._token_ok(request) or request.headers.get("Origin") != expected_origin:
            return web.Response(status=403, text="No autorizado")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._sockets.add(ws)
        try:
            await ws.send_json({"type": "state", "state": self._presenter.state})
            async for message in ws:
                if message.type == WSMsgType.ERROR:
                    break
        finally:
            self._sockets.discard(ws)
        return ws

    async def _broadcast(self, state: State) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_json({"type": "state", "state": state})
            except (ConnectionError, RuntimeError):
                self._sockets.discard(ws)

    # --- Iconos (recursos del juego, a través del cliente) ------------------------------

    async def _item_icon(self, request: web.Request) -> web.Response:
        return await self._icon("item", int(request.match_info["item_id"]))

    async def _champion_icon(self, request: web.Request) -> web.Response:
        return await self._icon("champion", int(request.match_info["champion_id"]))

    async def _icon(self, kind: str, ident: int) -> web.Response:
        body = await self._icons.get(kind, ident)
        if body is None:
            raise web.HTTPNotFound()
        return web.Response(
            body=body, content_type="image/png", headers={"Cache-Control": "max-age=86400"}
        )
