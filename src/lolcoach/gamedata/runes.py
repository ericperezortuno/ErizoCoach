"""Runas: árboles (estilos), páginas, recomendaciones de Riot y validación.

Una página tiene 9 runas en este orden:

    0      piedra angular (árbol principal)
    1-3    una runa de cada fila del árbol principal
    4-5    dos runas del árbol secundario, de filas distintas
    6-8    tres fragmentos (una de cada fila de fragmentos)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..models import Position
from ..util import as_dict, as_int, as_list, as_str

PAGE_SIZE = 9
KEYSTONE_INDEX = 0
PRIMARY_INDEXES = (1, 2, 3)
SECONDARY_INDEXES = (4, 5)
SHARD_INDEXES = (6, 7, 8)


@dataclass(frozen=True, slots=True)
class PerkStyle:
    id: int
    name: str
    rows: tuple[tuple[int, ...], ...]  # [piedras angulares, fila 1, fila 2, fila 3]
    shard_rows: tuple[tuple[int, ...], ...]  # 3 filas de fragmentos
    allowed_sub_styles: frozenset[int]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PerkStyle:
        rows: list[tuple[int, ...]] = []
        shards: list[tuple[int, ...]] = []
        for slot in as_list(data.get("slots")):
            slot = as_dict(slot)
            perks = tuple(as_int(p) for p in as_list(slot.get("perks")))
            if slot.get("type") == "kStatMod":
                shards.append(perks)
            else:
                rows.append(perks)
        return cls(
            id=as_int(data.get("id")),
            name=as_str(data.get("name")),
            rows=tuple(rows),
            shard_rows=tuple(shards),
            allowed_sub_styles=frozenset(as_int(s) for s in as_list(data.get("allowedSubStyles"))),
        )

    def row_of(self, perk_id: int) -> int | None:
        for index, row in enumerate(self.rows):
            if perk_id in row:
                return index
        return None


@dataclass(frozen=True, slots=True)
class RunePage:
    primary_style_id: int
    sub_style_id: int
    perk_ids: tuple[int, ...]

    @property
    def keystone_id(self) -> int:
        return self.perk_ids[KEYSTONE_INDEX] if self.perk_ids else 0

    def with_perk(self, index: int, perk_id: int) -> RunePage:
        perks = list(self.perk_ids)
        perks[index] = perk_id
        return RunePage(self.primary_style_id, self.sub_style_id, tuple(perks))

    def to_api(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "primaryStyleId": self.primary_style_id,
            "subStyleId": self.sub_style_id,
            "selectedPerkIds": list(self.perk_ids),
            "current": True,
        }


@dataclass(frozen=True, slots=True)
class RecommendedPage:
    """Página recomendada por Riot (``/lol-perks/v1/recommended-pages/...``)."""

    page: RunePage
    spells: tuple[int, ...]
    position: Position
    keystone_name: str
    descriptor: str  # p. ej. "Daño de combos"
    recommendation_id: str

    @classmethod
    def from_json(cls, data: Any) -> RecommendedPage | None:
        raw = as_dict(data)
        # Cada runa llega como objeto ({"id": 8112, "name": ...}); toleramos también números.
        perks = tuple(
            as_int(p.get("id")) if isinstance(p, dict) else as_int(p)
            for p in as_list(raw.get("perks"))
        )
        if any(p <= 0 for p in perks):
            return None
        if len(perks) != PAGE_SIZE:
            return None
        keystone = as_dict(raw.get("keystone"))
        return cls(
            page=RunePage(
                primary_style_id=as_int(raw.get("primaryPerkStyleId")),
                sub_style_id=as_int(raw.get("secondaryPerkStyleId")),
                perk_ids=perks,
            ),
            spells=tuple(as_int(s) for s in as_list(raw.get("summonerSpellIds")))[:2],
            position=Position.parse(raw.get("position")),
            keystone_name=as_str(keystone.get("name")),
            descriptor=as_str(keystone.get("recommendationDescriptor")),
            recommendation_id=as_str(raw.get("recommendationId")),
        )


def validate_page(page: RunePage, styles: Mapping[int, PerkStyle]) -> list[str]:
    """Devuelve los problemas de la página (lista vacía = válida). Evita enviar páginas rotas."""
    primary = styles.get(page.primary_style_id)
    sub = styles.get(page.sub_style_id)
    if primary is None:
        return [f"árbol principal desconocido: {page.primary_style_id}"]
    if sub is None:
        return [f"árbol secundario desconocido: {page.sub_style_id}"]
    problems: list[str] = []
    if sub.id == primary.id:
        problems.append("el árbol secundario no puede ser el principal")
    elif primary.allowed_sub_styles and sub.id not in primary.allowed_sub_styles:
        problems.append(f"{sub.name} no se puede combinar con {primary.name}")
    if len(page.perk_ids) != PAGE_SIZE:
        return [*problems, f"se esperaban {PAGE_SIZE} runas y hay {len(page.perk_ids)}"]
    if len(primary.rows) != 4 or len(primary.shard_rows) != 3:
        return [*problems, f"estructura inesperada del árbol {primary.name}"]

    for row, index in enumerate((KEYSTONE_INDEX, *PRIMARY_INDEXES)):
        if page.perk_ids[index] not in primary.rows[row]:
            problems.append(
                f"la runa {page.perk_ids[index]} no está en la fila {row} de {primary.name}"
            )
    sub_rows = [sub.row_of(page.perk_ids[i]) for i in SECONDARY_INDEXES]
    if any(row is None or row == 0 for row in sub_rows):
        problems.append(f"runas secundarias no válidas para {sub.name}")
    elif sub_rows[0] == sub_rows[1]:
        problems.append("las dos runas secundarias son de la misma fila")
    for row, index in enumerate(SHARD_INDEXES):
        if page.perk_ids[index] not in primary.shard_rows[row]:
            problems.append(f"fragmento {page.perk_ids[index]} no válido en la fila {row + 1}")
    return problems
