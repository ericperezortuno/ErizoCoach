"""Botas en el espacio de la misión de rol de la línea inferior (deducidas).

Al completar la misión de rol de tirador, las botas pasan a un espacio propio, aparte del
inventario. La API de la partida en vivo (``/liveclientdata``) solo publica los espacios
0-6, así que esas botas no aparecen en ningún sitio, y tampoco las que se compran después.
Sin esto, LoL Coach seguía pidiendo «compra ahora tus botas» aunque ya las tuvieras.

Solo si juegas en la línea inferior (la única misión con ese espacio), las botas se dan
por compradas para el resto de la partida:

* si unas botas que estaban en tu inventario desaparecen sin que aparezcan otras (se han
  movido a ese espacio; básicas o no, la mejora también se compra allí, sin verse), o
* si, sin haber tenido botas a la vista, gastas oro que no explica ningún objeto nuevo
  (al menos ``MIN_HIDDEN_SPEND``): las has comprado directamente en ese espacio.
"""

from __future__ import annotations

import logging
from collections import Counter

from ..models import Position
from .advisor import ItemLookup
from .models import LivePlayer

log = logging.getLogger(__name__)

# Oro gastado sin ningún objeto nuevo a la vista a partir del que son unas botas ocultas.
# Lo más barato son unas Botas (300) o completar unas a partir de sus componentes (~200);
# con 3 s entre lecturas, el oro que se gana mientras tanto no llega a esto.
MIN_HIDDEN_SPEND = 150


class RoleSlotBoots:
    """Sigue tus objetos lectura a lectura durante una partida (una instancia por partida)."""

    def __init__(self, item: ItemLookup) -> None:
        self._item = item
        self._previous: tuple[Counter[int], float] | None = None
        self._seen_boots = False  # has tenido botas a la vista en esta partida
        self.hidden = False

    def update(self, me: LivePlayer, gold: float) -> bool:
        """Registra una lectura. Devuelve True si tienes botas que la API no muestra."""
        items: Counter[int] = Counter()
        for entry in me.items:  # el mismo objeto puede estar en varios huecos
            items[entry.item_id] += entry.count
        has_boots = any(self._is_boots(i) for i in items)
        if not self.hidden and self._previous is not None and me.position == Position.BOTTOM:
            before, before_gold = self._previous
            if not has_boots and any(self._is_boots(i) for i in before):
                self._hide("tus botas han pasado al espacio de la misión de rol")
            elif not has_boots and not self._seen_boots:
                new = items - before  # objetos que han aparecido (o de los que tienes más)
                unexplained = before_gold - gold - sum(self._price(i) for i in new.elements())
                if unexplained >= MIN_HIDDEN_SPEND:
                    self._hide(f"{unexplained:.0f} de oro gastados sin ningún objeto nuevo")
        self._seen_boots = self._seen_boots or has_boots
        self._previous = (items, gold)
        return self.hidden

    def _hide(self, why: str) -> None:
        self.hidden = True
        log.info("Botas en el espacio de la misión de rol (la API no lo muestra): %s", why)

    def _is_boots(self, item_id: int) -> bool:
        found = self._item(item_id)
        return found is not None and found.is_boots

    def _price(self, item_id: int) -> int:
        found = self._item(item_id)
        return found.price_total if found is not None else 0
