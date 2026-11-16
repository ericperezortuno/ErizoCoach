"""Cliente de LoL simulado: HTTPS + autenticación + websocket WAMP, como el real.

Incluye las páginas de runas (con su límite de huecos), los hechizos de la
selección y los datos reales del juego grabados del parche 16.22.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import ssl
from typing import Any

from aiohttp import WSMsgType, web

from erizocoach.lcu import endpoints
from erizocoach.lcu.websocket import WAMP_EVENT, WAMP_SUBSCRIBE, topic_for

from .gamedata_fixture import static_route

QUEUE = {
    "id": 440,
    "description": "Clasificatoria flexible",
    "gameMode": "CLASSIC",
    "type": "RANKED_FLEX_SR",
    "pickMode": "TeamBuilderDraftPickStrategy",
    "isRanked": True,
    "mapId": 11,
}


def rune_page(page_id: int, name: str, *, valid: bool = True, temporary: bool = False) -> dict:
    return {
        "id": page_id,
        "name": name,
        "isEditable": True,
        "isDeletable": True,
        "isTemporary": temporary,
        "isValid": valid,
        "isActive": False,
        "current": False,
        "primaryStyleId": 8000 if valid else 8300,
        "subStyleId": 8100 if valid else -1,
        "selectedPerkIds": [8005, 9111, 9104, 8014, 8143, 8135, 5008, 5008, 5011] if valid else [],
    }


def user_item_set(uid: str, title: str) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "type": "custom",
        "map": "any",
        "mode": "any",
        "sortrank": 0,
        "startedFrom": "blank",
        "associatedChampions": [],
        "associatedMaps": [11, 12],
        "preferredItemSlots": [],
        "blocks": [{"type": "Mis objetos", "items": [{"id": "3089", "count": 1}],
                    "hideIfSummonerSpell": "", "showIfSummonerSpell": ""}],
    }  # fmt: skip


class FakeLcu:
    def __init__(self, password: str = "fake-pass-123") -> None:
        self.password = password
        self.phase = "Lobby"
        self.session: dict[str, Any] | None = None
        self.ready = True  # False = el cliente acaba de abrirse y aún no responde
        self.requests: list[str] = []
        self.writes: list[tuple[str, str, Any]] = []  # (método, ruta, cuerpo)
        self.subscriptions: set[str] = set()
        self.port = 0
        # Runas: por defecto, 2 páginas tuyas y un hueco libre
        self.pages: list[dict[str, Any]] = [rune_page(11, "Smolder"), rune_page(12, "Kai'sa")]
        self.page_slots = 3
        self.current_page_id = 11
        self._next_page_id = 500
        # Conjuntos de objetos: uno tuyo (como el que tienes en tu cuenta)
        self.summoner_id = 4242
        self.item_sets: list[dict[str, Any]] = [user_item_set("user-set-1", "Mi conjunto")]
        self.drop_other_item_sets = False  # simula un fallo del cliente al guardar
        self._sockets: list[web.WebSocketResponse] = []
        self._runner: web.AppRunner | None = None

        app = web.Application(middlewares=[self._auth])
        r = app.router
        r.add_get("/", self._websocket)
        r.add_get(endpoints.GAMEFLOW_PHASE, self._gameflow_phase)
        r.add_get(endpoints.SYSTEM_BUILDS, self._json({"version": "16.22.834.8910"}))
        r.add_get(endpoints.REGION_LOCALE, self._json({"region": "EUW", "locale": "es_ES"}))
        r.add_get(
            endpoints.GAMEFLOW_SESSION,
            self._json({"gameData": {"queue": QUEUE}, "map": {"id": 11}}),
        )
        r.add_get(endpoints.CHAMP_SELECT_SESSION, self._champ_select)
        r.add_patch(endpoints.CHAMP_SELECT_MY_SELECTION, self._my_selection)
        r.add_get(endpoints.PERK_PAGES, self._list_pages)
        r.add_post(endpoints.PERK_PAGES, self._create_page)
        r.add_put("/lol-perks/v1/pages/{page_id}", self._update_page)
        r.add_delete("/lol-perks/v1/pages/{page_id}", self._delete_page)
        r.add_get(endpoints.PERK_INVENTORY, self._inventory)
        r.add_get(endpoints.PERK_CURRENT_PAGE, self._get_current_page)
        r.add_put(endpoints.PERK_CURRENT_PAGE, self._set_current_page)
        r.add_get(endpoints.CURRENT_SUMMONER, self._current_summoner)
        r.add_get("/lol-item-sets/v1/item-sets/{summoner_id}/sets", self._get_item_sets)
        r.add_put("/lol-item-sets/v1/item-sets/{summoner_id}/sets", self._put_item_sets)
        r.add_get("/{tail:.*}", self._static)  # datos del juego grabados
        self._app = app

    # --- Servidor -------------------------------------------------------------

    async def start(self, ssl_context: ssl.SSLContext) -> int:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=ssl_context)
        await site.start()
        self.port = self._runner.addresses[0][1]
        return self.port

    async def stop(self) -> None:
        await self.drop_connections()
        if self._runner is not None:
            await self._runner.cleanup()
        # En Windows (Proactor) el cierre de las conexiones termina en la siguiente vuelta
        # del bucle: se espera un instante para que no queden transportes sin cerrar.
        await asyncio.sleep(0.05)

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        token = base64.b64encode(f"riot:{self.password}".encode()).decode()
        if request.headers.get("Authorization") != f"Basic {token}":
            return web.json_response({"message": "Unauthorized"}, status=401)
        self.requests.append(request.path)
        if request.method != "GET":
            body = await request.json() if request.can_read_body else None
            self.writes.append((request.method, request.path, body))
        return await handler(request)

    @staticmethod
    def _json(payload: Any) -> Any:
        async def handler(_request: web.Request) -> web.Response:
            return web.json_response(payload)

        return handler

    @staticmethod
    def _error(status: int, message: str) -> web.Response:
        return web.json_response({"httpStatus": status, "message": message}, status=status)

    async def _static(self, request: web.Request) -> web.Response:
        data = static_route(request.path)
        if data is None:
            return self._error(404, "Not found")
        return web.json_response(data)

    async def _gameflow_phase(self, _request: web.Request) -> web.Response:
        if not self.ready:
            return self._error(404, "No active delegate")
        return web.json_response(self.phase)

    async def _champ_select(self, _request: web.Request) -> web.Response:
        if self.session is None:
            return self._error(404, "No active delegate")
        return web.json_response(self.session)

    async def _my_selection(self, request: web.Request) -> web.Response:
        if self.session is None:
            return self._error(404, "No active delegate")
        body = await request.json()
        local = self.session["localPlayerCellId"]
        for slot in self.session["myTeam"]:
            if slot["cellId"] == local:
                slot["spell1Id"] = body.get("spell1Id", slot["spell1Id"])
                slot["spell2Id"] = body.get("spell2Id", slot["spell2Id"])
        await self.push(endpoints.CHAMP_SELECT_SESSION, self.session)
        return web.Response(status=204)

    # --- Runas ---------------------------------------------------------------------

    def _custom_count(self) -> int:
        return sum(not p["isTemporary"] for p in self.pages)

    def _views(self) -> list[dict[str, Any]]:
        return [p | {"current": p["id"] == self.current_page_id} for p in self.pages]

    def page(self, page_id: int) -> dict[str, Any] | None:
        return next((p for p in self.pages if p["id"] == page_id), None)

    async def _list_pages(self, _request: web.Request) -> web.Response:
        return web.json_response(self._views())

    async def _inventory(self, _request: web.Request) -> web.Response:
        free = self._custom_count() < self.page_slots
        return web.json_response(
            {"canAddCustomPage": free, "customPageCount": self._custom_count(),
             "ownedPageCount": self.page_slots}
        )  # fmt: skip

    async def _create_page(self, request: web.Request) -> web.Response:
        if self._custom_count() >= self.page_slots:
            return self._error(400, "Max pages reached")
        body = await request.json()
        self._next_page_id += 1
        page = rune_page(self._next_page_id, body["name"]) | self._content(body)
        self.pages.append(page)
        if body.get("current"):
            self.current_page_id = page["id"]
        return web.json_response(page | {"current": page["id"] == self.current_page_id})

    async def _update_page(self, request: web.Request) -> web.Response:
        page = self.page(int(request.match_info["page_id"]))
        if page is None:
            return self._error(404, "Page not found")
        body = await request.json()
        page.update({"name": body.get("name", page["name"])} | self._content(body))
        return web.json_response(page)

    async def _delete_page(self, request: web.Request) -> web.Response:
        page = self.page(int(request.match_info["page_id"]))
        if page is None:
            return self._error(404, "Page not found")
        self.pages.remove(page)
        return web.Response(status=204)

    async def _get_current_page(self, _request: web.Request) -> web.Response:
        page = self.page(self.current_page_id)
        if page is None:
            return self._error(404, "No current page")
        return web.json_response(page | {"current": True})

    async def _set_current_page(self, request: web.Request) -> web.Response:
        page_id = await request.json()
        if self.page(page_id) is None:
            return self._error(404, "Page not found")
        self.current_page_id = page_id
        return web.Response(status=204)

    @staticmethod
    def _content(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "primaryStyleId": body["primaryStyleId"],
            "subStyleId": body["subStyleId"],
            "selectedPerkIds": list(body["selectedPerkIds"]),
            "isValid": True,
        }

    # --- Invocador y conjuntos de objetos ---------------------------------------------

    async def _current_summoner(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"summonerId": self.summoner_id, "accountId": 777, "gameName": "Jugador",
             "tagLine": "EUW", "puuid": "00000000-0000-0000-0000-000000000000"}
        )  # fmt: skip

    def _check_summoner(self, request: web.Request) -> web.Response | None:
        if int(request.match_info["summoner_id"]) != self.summoner_id:
            return self._error(403, "Not your summoner")
        return None

    async def _get_item_sets(self, request: web.Request) -> web.Response:
        if (error := self._check_summoner(request)) is not None:
            return error
        return web.json_response({"accountId": 777, "itemSets": self.item_sets, "timestamp": 1})

    async def _put_item_sets(self, request: web.Request) -> web.Response:
        if (error := self._check_summoner(request)) is not None:
            return error
        body = await request.json()
        sets = copy.deepcopy(body["itemSets"])
        if self.drop_other_item_sets:
            sets = [s for s in sets if s["title"].startswith("Erizo Coach")]
            self.drop_other_item_sets = False  # solo falla una vez
        self.item_sets = sets
        return web.Response(status=201)

    def add_temporary_page(self, *, make_current: bool = True) -> int:
        """Simula la página temporal que el cliente crea al bloquear un campeón."""
        self._next_page_id += 1
        self.pages.append(rune_page(self._next_page_id, "Recomendada", temporary=True))
        if make_current:
            self.current_page_id = self._next_page_id
        return self._next_page_id

    def writes_to(self, prefix: str) -> list[tuple[str, str, Any]]:
        return [w for w in self.writes if w[1].startswith(prefix)]

    # --- Websocket -----------------------------------------------------------------

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=("wamp",))
        await ws.prepare(request)
        self._sockets.append(ws)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                data = json.loads(message.data)
                if data[0] == WAMP_SUBSCRIBE:
                    self.subscriptions.add(data[1])
        self._sockets.remove(ws)
        return ws

    # --- Control desde los tests ---------------------------------------------

    async def push(self, uri: str, data: Any, event_type: str = "Update") -> None:
        topic = topic_for(uri)
        if topic not in self.subscriptions:
            return
        message = json.dumps(
            [WAMP_EVENT, topic, {"data": data, "eventType": event_type, "uri": uri}]
        )
        for ws in list(self._sockets):
            await ws.send_str(message)

    async def set_phase(self, phase: str) -> None:
        self.phase = phase
        await self.push(endpoints.GAMEFLOW_PHASE, phase)

    async def set_session(self, session: dict[str, Any]) -> None:
        event_type = "Create" if self.session is None else "Update"
        self.session = copy.deepcopy(session)
        await self.push(endpoints.CHAMP_SELECT_SESSION, self.session, event_type)

    async def delete_session(self) -> None:
        self.session = None
        await self.push(endpoints.CHAMP_SELECT_SESSION, None, "Delete")

    async def drop_connections(self) -> None:
        """Simula que el cliente se cierra."""
        for ws in list(self._sockets):
            await ws.close()
