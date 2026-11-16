"""Modelos de la build."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..models import Position


class Archetype(StrEnum):
    MAGE_BURST = "mage_burst"
    MAGE_SUSTAINED = "mage_sustained"
    AP_FIGHTER = "ap_fighter"
    MARKSMAN_CRIT = "marksman_crit"
    MARKSMAN_ONHIT = "marksman_onhit"
    ASSASSIN_AD = "assassin_ad"
    FIGHTER_AD = "fighter_ad"
    TANK = "tank"
    SUPPORT_ENCHANTER = "support_enchanter"
    SUPPORT_TANK = "support_tank"


ARCHETYPE_LABELS = {
    Archetype.MAGE_BURST: "mago de daño explosivo",
    Archetype.MAGE_SUSTAINED: "mago de daño continuado",
    Archetype.AP_FIGHTER: "luchador de poder de habilidad",
    Archetype.MARKSMAN_CRIT: "tirador de críticos",
    Archetype.MARKSMAN_ONHIT: "tirador de efectos al impacto",
    Archetype.ASSASSIN_AD: "asesino de daño físico",
    Archetype.FIGHTER_AD: "luchador de daño físico",
    Archetype.TANK: "tanque",
    Archetype.SUPPORT_ENCHANTER: "apoyo encantador",
    Archetype.SUPPORT_TANK: "apoyo tanque",
}


@dataclass(frozen=True, slots=True)
class ItemCount:
    item_id: int
    count: int = 1


@dataclass(frozen=True, slots=True)
class BaseBuild:
    """Build de partida para un campeón, antes de adaptarla a los enemigos."""

    archetype: Archetype
    source: str  # de dónde sale: "reglas" (y, en el futuro, "estadísticas")
    starting: tuple[ItemCount, ...]
    boots: int | None  # None = el campeón no usa botas (Cassiopeia)
    core: tuple[int, ...]  # 3 objetos, en orden de compra
    extra: tuple[int, ...]  # siguientes objetos por defecto
    support_upgrade: int | None = None  # mejora del objeto de apoyo


@dataclass(frozen=True, slots=True)
class SituationalItem:
    item_id: int
    reason: str
    component_id: int | None = None  # componente barato para comprar pronto


@dataclass(frozen=True, slots=True)
class BuildPlan:
    """Build final para tu partida, con el porqué de cada cambio."""

    champion_id: int
    position: Position
    map_id: int
    archetype: Archetype
    source: str
    starting: tuple[ItemCount, ...]
    boots: int | None
    core: tuple[int, ...]
    full: tuple[int, ...]  # build completa de 6 objetos (botas incluidas)
    situational: tuple[SituationalItem, ...] = ()
    support_upgrade: int | None = None
    adjustments: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
