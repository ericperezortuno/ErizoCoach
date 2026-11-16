"""Escribe la build como conjunto de objetos de la tienda de la partida.

Seguridad con tus conjuntos de objetos:

* La API del cliente solo permite **reemplazar la lista completa**, así que se
  lee la lista actual, se cambia **solo** el conjunto de Erizo Coach (título que
  empieza por "Erizo Coach") y se conservan los tuyos tal cual.
* Después se vuelve a leer para comprobar que tus conjuntos siguen ahí. Si faltara
  alguno, se restaura la lista original y se avisa.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..lcu import endpoints
from ..lcu.client import LcuClient, LcuError
from ..util import as_dict, as_int, as_list, as_str
from .models import BuildPlan, ItemCount

log = logging.getLogger(__name__)

SET_PREFIX = "Erizo Coach"
# Hasta la 0.18 la app se llamaba «LoL Coach»: ese conjunto sigue siendo nuestro y se
# sustituye por el nuevo (no se queda uno viejo en tu tienda).
OWN_PREFIXES = (SET_PREFIX, "LoL Coach")
MAX_TITLE = 25


class ItemSetError(LcuError):
    """No se pudo escribir el conjunto de objetos sin poner en riesgo los tuyos."""


def set_title(champion_name: str) -> str:
    return f"{SET_PREFIX}: {champion_name}"[:MAX_TITLE]


def is_ours(item_set: Any) -> bool:
    raw = as_dict(item_set)
    is_custom = raw.get("type", "custom") == "custom"
    return is_custom and as_str(raw.get("title")).startswith(OWN_PREFIXES)


def _block(title: str, items: list[ItemCount]) -> dict[str, Any]:
    return {
        "type": title,
        "items": [{"id": str(i.item_id), "count": i.count} for i in items],
        "hideIfSummonerSpell": "",
        "showIfSummonerSpell": "",
    }


def build_item_set(plan: BuildPlan, uid: str, title: str) -> dict[str, Any]:
    """Bloques que verás en la tienda, en este orden."""
    blocks = [_block("Inicio", list(plan.starting))]
    if plan.boots is not None:
        blocks.append(_block("Botas", [ItemCount(plan.boots)]))
    blocks.append(_block("Núcleo (en orden)", [ItemCount(i) for i in plan.core]))
    if plan.situational:
        situational: list[ItemCount] = []
        for need in plan.situational:
            if need.component_id is not None:
                situational.append(ItemCount(need.component_id))
            situational.append(ItemCount(need.item_id))
        blocks.append(_block("Contra este equipo", situational))
    blocks.append(_block("Build completa", [ItemCount(i) for i in plan.full]))
    if plan.support_upgrade is not None:
        blocks.append(_block("Mejora de apoyo", [ItemCount(plan.support_upgrade)]))
    return {
        "uid": uid,
        "title": title,
        "type": "custom",
        "map": "any",
        "mode": "any",
        "sortrank": 0,
        "startedFrom": "blank",
        "associatedChampions": [plan.champion_id],
        "associatedMaps": [plan.map_id],
        "preferredItemSlots": [],
        "blocks": blocks,
    }


class ItemSetWriter:
    async def summoner_id(self, client: LcuClient) -> int:
        """Id de tu invocador. Del resto de la respuesta (Riot ID...) no se guarda nada."""
        summoner = as_dict(await client.get(endpoints.CURRENT_SUMMONER))
        summoner_id = as_int(summoner.get("summonerId"))
        if summoner_id <= 0:
            raise ItemSetError("el cliente no ha devuelto tu id de invocador")
        return summoner_id

    async def write(self, client: LcuClient, summoner_id: int, plan: BuildPlan, title: str) -> str:
        path = endpoints.ITEM_SETS.format(summoner_id=summoner_id)
        current = as_dict(await client.get(path))
        if "itemSets" not in current:
            raise ItemSetError("respuesta inesperada del cliente al leer tus conjuntos")
        sets = [s for s in as_list(current.get("itemSets")) if isinstance(s, dict)]
        ours = [s for s in sets if is_ours(s)]
        yours = [s for s in sets if not is_ours(s)]
        uid = as_str(ours[0].get("uid")) if ours else ""
        new_set = build_item_set(plan, uid or str(uuid.uuid4()), title)

        await client.put(path, {**current, "itemSets": [new_set, *yours], "timestamp": _now()})

        after = as_dict(await client.get(path))
        kept = {as_str(s.get("uid")) for s in as_list(after.get("itemSets")) if isinstance(s, dict)}
        lost = [s for s in yours if as_str(s.get("uid")) not in kept]
        if lost or new_set["uid"] not in kept:
            await client.put(path, {**current, "timestamp": _now()})
            raise ItemSetError(
                "el cliente no guardó bien los conjuntos de objetos; se ha restaurado tu lista"
            )
        log.debug("Conjunto «%s» guardado (%d conjuntos tuyos conservados)", title, len(yours))
        return new_set["uid"]


def _now() -> int:
    return int(time.time() * 1000)
