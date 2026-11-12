"""Objetos del juego (``/lol-game-data/assets/v1/items.json``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..util import as_int, as_list, as_str

# Los objetos de la Grieta tienen id < 10000; los de otros modos (Arena, etc.) usan rangos altos.
RIFT_ITEM_ID_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class Item:
    id: int
    name: str
    price_total: int
    from_ids: tuple[int, ...]  # componentes
    into_ids: tuple[int, ...]  # objetos que lo usan como componente
    categories: frozenset[str]  # "SpellDamage", "Armor", "SpellBlock", "Boots"...
    in_store: bool
    display_in_item_sets: bool
    required_champion: str
    required_ally: str

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Item:
        return cls(
            id=as_int(data.get("id")),
            name=as_str(data.get("name")),
            price_total=as_int(data.get("priceTotal")),
            from_ids=tuple(as_int(i) for i in as_list(data.get("from"))),
            into_ids=tuple(as_int(i) for i in as_list(data.get("to"))),
            categories=frozenset(c for c in as_list(data.get("categories")) if isinstance(c, str)),
            in_store=bool(data.get("inStore")),
            display_in_item_sets=bool(data.get("displayInItemSets")),
            required_champion=as_str(data.get("requiredChampion")),
            required_ally=as_str(data.get("requiredAlly")),
        )

    @property
    def buyable(self) -> bool:
        """Se puede comprar en la tienda de la Grieta y mostrar en un conjunto de objetos."""
        return (
            0 < self.id < RIFT_ITEM_ID_LIMIT
            and self.in_store
            and self.display_in_item_sets
            and not self.required_champion
            and not self.required_ally
        )

    @property
    def is_boots(self) -> bool:
        return "Boots" in self.categories
