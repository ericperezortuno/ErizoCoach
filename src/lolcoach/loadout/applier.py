"""Aplica runas y hechizos en el cliente.

Reglas de seguridad con tus páginas de runas:

* La app usa **una sola página propia**, cuyo nombre empieza por "LoL Coach".
* Si no existe: la crea si tienes un hueco libre; si no, reutiliza una página
  **vacía y no válida** (sin nombre). Nunca toca ninguna otra página tuya.
* Si no hay ninguna de las dos opciones, no cambia nada y avisa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..gamedata.runes import RunePage
from ..lcu import endpoints
from ..lcu.client import LcuClient, LcuError, LcuHttpError
from ..util import as_dict, as_int, as_list, as_str

log = logging.getLogger(__name__)

PAGE_PREFIX = "LoL Coach"
MAX_PAGE_NAME = 25  # límite del cliente para el nombre de una página


class NoRunePageSlot(LcuError):
    """No hay página propia, ni hueco libre, ni página vacía que reutilizar."""


@dataclass(frozen=True, slots=True)
class PageInfo:
    id: int
    name: str
    editable: bool
    temporary: bool
    valid: bool
    current: bool

    @classmethod
    def from_json(cls, data: Any) -> PageInfo:
        raw = as_dict(data)
        return cls(
            id=as_int(raw.get("id")),
            name=as_str(raw.get("name")),
            editable=bool(raw.get("isEditable")),
            temporary=bool(raw.get("isTemporary")),
            valid=bool(raw.get("isValid")),
            current=bool(raw.get("current")),
        )

    @property
    def is_ours(self) -> bool:
        return self.editable and not self.temporary and self.name.startswith(PAGE_PREFIX)

    @property
    def is_empty(self) -> bool:
        """Página sin nombre y sin runas válidas: no se pierde nada al reutilizarla."""
        return self.editable and not self.temporary and not self.name.strip() and not self.valid


def page_title(champion_name: str) -> str:
    return f"{PAGE_PREFIX}: {champion_name}"[:MAX_PAGE_NAME]


class LoadoutApplier:
    async def apply_runes(self, client: LcuClient, page: RunePage, title: str) -> int:
        """Escribe la página, la pone como activa y devuelve su id."""
        pages = [PageInfo.from_json(p) for p in as_list(await client.get(endpoints.PERK_PAGES))]
        body = page.to_api(title)
        ours = next((p for p in pages if p.is_ours), None)
        if ours is not None:
            page_id = await self._overwrite(client, ours.id, body)
        else:
            inventory = as_dict(await client.get(endpoints.PERK_INVENTORY))
            if inventory.get("canAddCustomPage"):
                page_id = as_int(as_dict(await client.post(endpoints.PERK_PAGES, body)).get("id"))
                log.info("Creada la página de runas «%s»", title)
            else:
                empty = next((p for p in pages if p.is_empty), None)
                if empty is None:
                    raise NoRunePageSlot("no hay hueco para la página de runas de LoL Coach")
                page_id = await self._overwrite(client, empty.id, body)
                log.info("La página vacía se ha convertido en «%s»", title)
        if page_id <= 0:
            raise LcuError("el cliente no devolvió el id de la página")
        await client.put(endpoints.PERK_CURRENT_PAGE, page_id)
        return page_id

    async def _overwrite(self, client: LcuClient, page_id: int, body: dict[str, Any]) -> int:
        try:
            await client.put(endpoints.PERK_PAGE.format(page_id=page_id), {**body, "id": page_id})
            return page_id
        except LcuHttpError as exc:
            # Algunas versiones del cliente no permiten editar: se borra y se crea de nuevo
            # (solo se hace con nuestra página o con la página vacía).
            log.debug("No se pudo editar la página %s (%s); se recrea", page_id, exc)
            await client.delete(endpoints.PERK_PAGE.format(page_id=page_id))
            created = as_dict(await client.post(endpoints.PERK_PAGES, body))
            return as_int(created.get("id"))

    async def ensure_current_page(self, client: LcuClient, page_id: int) -> bool:
        """Si el cliente ha puesto su página temporal encima de la nuestra, la recupera.

        Si la página activa es una tuya (la has elegido a mano), se respeta.
        Devuelve True si ha tenido que corregirla.
        """
        current = PageInfo.from_json(await client.get(endpoints.PERK_CURRENT_PAGE))
        if current.id == page_id or not current.temporary:
            return False
        await client.put(endpoints.PERK_CURRENT_PAGE, page_id)
        return True

    async def apply_spells(self, client: LcuClient, spells: tuple[int, int]) -> None:
        await client.patch(
            endpoints.CHAMP_SELECT_MY_SELECTION, {"spell1Id": spells[0], "spell2Id": spells[1]}
        )
