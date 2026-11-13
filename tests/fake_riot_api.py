"""API de Riot simulada (formato de match-v5, timeline y league-v4) para las pruebas."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

API_KEY = "RGAPI-00000000-1111-2222-3333-444444444444"

# Compras típicas (segundos, objeto) por campeón: (champ_id, posición, compras)
AHRI_MID = [(5, 1056), (6, 2003), (6, 2003), (420, 3802), (600, 3020), (900, 6655),
            (1300, 4645), (1700, 3089), (2000, 3135)]  # fmt: skip
ZED_MID = [(5, 1055), (6, 2003), (500, 3158), (800, 3142), (1200, 6697), (1600, 6694),
           (1900, 3814)]  # fmt: skip
FILLER = [(5, 1055), (6, 2003), (700, 3047), (1000, 3078)]

# Runas de Ahri en formato match-v5: Electrocutar (Dominación) + Brujería
AHRI_PERKS = {
    "statPerks": {"defense": 5001, "flex": 5008, "offense": 5005},
    "styles": [
        {"description": "primaryStyle", "style": 8100,
         "selections": [{"perk": p, "var1": 0} for p in (8112, 8139, 8140, 8106)]},
        {"description": "subStyle", "style": 8200,
         "selections": [{"perk": p, "var1": 0} for p in (8226, 8210)]},
    ],
}  # fmt: skip
AHRI_RUNES = (8100, 8200, 8112, 8139, 8140, 8106, 8226, 8210, 5005, 5008, 5001)


def participant(pid: int, champion: int, position: str, win: bool, items: list[int]) -> dict:
    data = {
        "participantId": pid,
        "puuid": f"puuid-secreto-{pid}",
        "riotIdGameName": f"Jugador{pid}",
        "riotIdTagline": "EUW",
        "summonerName": f"Jugador{pid}",
        "championId": champion,
        "championName": str(champion),
        "teamPosition": position,
        "win": win,
        "gameEndedInEarlySurrender": False,
        "summoner1Id": 4,
        "summoner2Id": 14,
        "perks": AHRI_PERKS,
    }
    for slot in range(7):
        data[f"item{slot}"] = items[slot] if slot < len(items) else 0
    return data


def build_match(
    match_id: str, version: str, *, duration: int = 1800, ahri_wins: bool = True
) -> tuple[dict, dict]:
    positions = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    plans = {3: (103, AHRI_MID), 8: (238, ZED_MID)}
    participants, events = [], []
    for pid in range(1, 11):
        champion, purchases = plans.get(pid, (100 + pid, FILLER))
        win = ahri_wins if pid <= 5 else not ahri_wins
        final = [item for _, item in purchases if item not in (2003,)][-6:]
        participants.append(participant(pid, champion, positions[(pid - 1) % 5], win, final))
        for second, item in purchases:
            events.append({"type": "ITEM_PURCHASED", "participantId": pid, "itemId": item,
                           "timestamp": second * 1000})  # fmt: skip
    # Ahri compra y deshace un objeto: no debe contar
    events.append(
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 3157, "timestamp": 2100_000}
    )
    events.append({"type": "ITEM_UNDO", "participantId": 3, "beforeId": 3157, "afterId": 0,
                   "timestamp": 2101_000})  # fmt: skip
    match = {
        "metadata": {"matchId": match_id, "participants": [p["puuid"] for p in participants]},
        "info": {"gameVersion": version, "gameDuration": duration, "queueId": 420,
                 "participants": participants},
    }  # fmt: skip
    frames = [{"timestamp": 0, "events": events}]
    timeline = {
        "metadata": {"matchId": match_id},
        "info": {"frameInterval": 60000, "frames": frames},
    }
    return match, timeline


class FakeRiotApi:
    def __init__(self) -> None:
        self.players = [f"puuid-{n}" for n in range(3)]
        self.matches: dict[str, tuple[dict, dict]] = {}
        self.player_matches: dict[str, list[str]] = {}
        self.requests: list[str] = []
        self.headers_seen: list[dict[str, str]] = []
        self.rate_limited_once = False  # si True, la primera petición de partida devuelve 429
        self.expired = False
        self.unavailable = False  # si True, las partidas responden 503 (Riot caído)
        self._runner: web.AppRunner | None = None
        self.url = ""

        # 3 jugadores; 5 partidas del parche actual y 1 antigua por jugador (algunas compartidas)
        for n, puuid in enumerate(self.players):
            ids = []
            for k in range(5):
                match_id = f"EUW1_{(n * 3 + k):04d}"  # se solapan entre jugadores
                ids.append(match_id)
                self.matches.setdefault(
                    match_id, build_match(match_id, "16.22.834.8910", ahri_wins=k % 2 == 0)
                )
            old_id = f"EUW1_OLD{n}"
            ids.append(old_id)
            self.matches[old_id] = build_match(old_id, "16.21.700.1234")
            self.player_matches[puuid] = ids
        # Una partida demasiado corta (remake)
        self.player_matches[self.players[0]].insert(0, "EUW1_SHORT")
        self.matches["EUW1_SHORT"] = build_match("EUW1_SHORT", "16.22.834.8910", duration=200)

        app = web.Application(middlewares=[self._auth])
        app.router.add_get("/lol/status/v4/platform-data", self._status)
        app.router.add_get("/lol/league/v4/{league}/by-queue/{queue}", self._league)
        app.router.add_get("/lol/match/v5/matches/by-puuid/{puuid}/ids", self._ids)
        app.router.add_get("/lol/match/v5/matches/{match_id}", self._match)
        app.router.add_get("/lol/match/v5/matches/{match_id}/timeline", self._timeline)
        self._app = app

    async def start(self) -> str:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{self._runner.addresses[0][1]}"
        return self.url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        await asyncio.sleep(0.05)

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        self.requests.append(request.path_qs)
        self.headers_seen.append(dict(request.headers))
        if self.expired or request.headers.get("X-Riot-Token") != API_KEY:
            return web.json_response(
                {"status": {"message": "Forbidden", "status_code": 403}}, status=403
            )
        response = await handler(request)
        response.headers["X-App-Rate-Limit"] = "20:1,100:120"
        return response

    async def _status(self, _request: web.Request) -> web.Response:
        return web.json_response({"id": "EUW1", "name": "EU West"})

    async def _league(self, request: web.Request) -> web.Response:
        if request.match_info["league"] != "challengerleagues":
            return web.json_response({"tier": "X", "entries": []})
        entries = [{"puuid": p, "leaguePoints": 1000} for p in self.players]
        return web.json_response({"tier": "CHALLENGER", "entries": entries})

    async def _ids(self, request: web.Request) -> web.Response:
        return web.json_response(self.player_matches.get(request.match_info["puuid"], []))

    async def _match(self, request: web.Request) -> web.Response:
        if self.unavailable:
            return web.json_response({}, status=503)
        if self.rate_limited_once:
            self.rate_limited_once = False
            return web.json_response({}, status=429, headers={"Retry-After": "1"})
        found = self.matches.get(request.match_info["match_id"])
        return web.json_response(found[0]) if found else web.json_response({}, status=404)

    async def _timeline(self, request: web.Request) -> web.Response:
        found = self.matches.get(request.match_info["match_id"])
        return web.json_response(found[1]) if found else web.json_response({}, status=404)
