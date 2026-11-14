"""Botas en el espacio de la misión de rol de la línea inferior (deducidas).

Al completar la misión de rol de tirador, las botas pasan a un espacio propio, aparte del
inventario. La API de la partida en vivo (``/liveclientdata``) solo publica los espacios
0-6, así que esas botas no aparecen en ningún sitio, y tampoco las que se compran después.
Sin esto, LoL Coach seguía pidiendo «compra ahora tus botas» aunque ya las tuvieras.

Solo si juegas en la línea inferior (la única misión con ese espacio) se deduce qué botas
tienes ahí, y se distingue si son las de base (Botas, o el Calzado ligeramente mágico de
la runa) o ya mejoradas, porque las de base no completan las botas de tu build:

* Unas botas que estaban en tu inventario desaparecen sin que aparezcan otras: se han
  movido a ese espacio, tal como estaban (de base o mejoradas).
* Sin haber tenido botas a la vista, gastas oro que no explica ningún objeto nuevo (al
  menos ``MIN_HIDDEN_SPEND``): las has comprado directamente allí; con ``UPGRADED_SPEND``
  o más, ya mejoradas.
* Con unas de base ocultas, otro gasto así (al menos ``MIN_UPGRADE_SPEND``) es su mejora.
"""

from __future__ import annotations

import logging
from collections import Counter
from enum import StrEnum

from ..models import Position
from .advisor import ItemLookup
from .models import LivePlayer

log = logging.getLogger(__name__)

# Oro gastado sin ningún objeto nuevo a la vista a partir del que son unas botas ocultas.
# Lo más barato son unas Botas (300) o completar unas a partir de sus componentes (~200);
# entre dos lecturas, el oro que se gana mientras tanto no llega a esto.
MIN_HIDDEN_SPEND = 150
UPGRADED_SPEND = 500  # unas Botas cuestan 300: gastar esto o más de golpe es comprarlas mejoradas
MIN_UPGRADE_SPEND = 400  # mejorar unas Botas cuesta 600 o más (y hasta ~200 si ya hay parte)


class HiddenBoots(StrEnum):
    NONE = "none"  # no hay botas ocultas (o no juegas en la línea inferior)
    BASIC = "basic"  # unas de base: cuentan como el componente, no como tus botas
    UPGRADED = "upgraded"  # ya mejoradas: tus botas están compradas


class RoleSlotBoots:
    """Sigue tus objetos lectura a lectura durante una partida (una instancia por partida)."""

    def __init__(self, item: ItemLookup) -> None:
        self._item = item
        self._previous: tuple[Counter[int], float] | None = None
        self._seen_boots = False  # has tenido botas a la vista en esta partida
        self.hidden = HiddenBoots.NONE

    def update(self, me: LivePlayer, gold: float) -> HiddenBoots:
        """Registra una lectura y dice qué botas tienes que la API no muestra."""
        items: Counter[int] = Counter()
        for entry in me.items:  # el mismo objeto puede estar en varios huecos
            items[entry.item_id] += entry.count
        has_boots = any(self._is_boots(i) for i in items)
        if self._previous is not None and me.position == Position.BOTTOM:
            before, before_gold = self._previous
            new = items - before  # objetos que han aparecido (o de los que tienes más)
            unexplained = before_gold - gold - sum(self._price(i) for i in new.elements())
            if self.hidden == HiddenBoots.NONE and not has_boots:
                vanished = [i for i in before if self._is_boots(i)]
                if vanished:
                    upgraded = any(self._is_upgraded(i) for i in vanished)
                    self._hide(upgraded, "tus botas han pasado al espacio de la misión de rol")
                elif not self._seen_boots and unexplained >= MIN_HIDDEN_SPEND:
                    self._hide(
                        unexplained >= UPGRADED_SPEND,
                        f"{unexplained:.0f} de oro gastados sin ningún objeto nuevo",
                    )
            elif self.hidden == HiddenBoots.BASIC and unexplained >= MIN_UPGRADE_SPEND:
                self._hide(True, f"mejora de {unexplained:.0f} de oro en ese espacio")
        self._seen_boots = self._seen_boots or has_boots
        self._previous = (items, gold)
        return self.hidden

    def _hide(self, upgraded: bool, why: str) -> None:
        self.hidden = HiddenBoots.UPGRADED if upgraded else HiddenBoots.BASIC
        kind = "mejoradas" if upgraded else "de base"
        log.info("Botas %s en el espacio de la misión de rol (la API no lo muestra): %s", kind, why)

    def _is_boots(self, item_id: int) -> bool:
        found = self._item(item_id)
        return found is not None and found.is_boots

    def _is_upgraded(self, item_id: int) -> bool:
        found = self._item(item_id)
        return found is not None and found.is_upgraded_boots

    def _price(self, item_id: int) -> int:
        found = self._item(item_id)
        return found.price_total if found is not None else 0
