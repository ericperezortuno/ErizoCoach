"""¿Qué compro ahora? Sigue tu build y la adapta a cómo va la partida.

Orden de prioridad:

1. **Respuesta a la mayor amenaza**:
   * Defensa urgente si un enemigo que hace mucho daño va muy fuerte (o tu rival de
     línea va fuerte) y aún no tienes defensa contra su tipo de daño.
   * Antisanación si quien se cura mucho va fuerte y aún no la tienes.
   Si se dan las dos, va primero la que responde al enemigo más fuerte (en empate,
   la antisanación).
2. **Tu build**: el siguiente objeto que te falte, en orden (botas, núcleo, resto).

Además calcula cuánto oro te falta (descontando los componentes que ya tienes,
en cualquier nivel de la receta) y qué componentes puedes comprar ya.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from ..builds.models import BuildPlan
from ..builds.rules import ANTI_HEAL_ITEMS, HEALERS, MAGIC_DEFENSE, PHYSICAL_DEFENSE, item_for
from ..gamedata.catalog import DamageType
from ..gamedata.items import Item
from .analysis import GameReport, PlayerReport, Strength

BASIC_BOOTS = 1001
DEFENSIVE_ITEM_MIN_PRICE = 1000  # un componente pequeño no cuenta como "ya tengo defensa"
MIN_THREAT_DAMAGE = 2  # nivel de daño (0-3) a partir del que compensa defenderse
MAX_COMPONENTS = 3


@dataclass(frozen=True, slots=True)
class PurchaseAdvice:
    item_id: int
    reason: str
    remaining_gold: int  # lo que te falta, con tu oro actual
    can_buy_now: bool
    affordable_components: tuple[int, ...] = ()  # componentes que ya te puedes comprar


@dataclass(frozen=True, slots=True)
class BuildStep:
    item_id: int
    owned: bool


@dataclass(frozen=True, slots=True)
class Advice:
    next_purchase: PurchaseAdvice | None
    build: tuple[BuildStep, ...]
    message: str = ""  # p. ej. "Build completa"


ItemLookup = Callable[[int], Item | None]


@dataclass(frozen=True, slots=True)
class _Candidate:
    item_id: int
    reason: str
    threat_score: float


def advise(
    plan: BuildPlan,
    report: GameReport,
    owned_ids: tuple[int, ...],
    gold: float,
    item: ItemLookup,
    champion_name: Callable[[int], str],
) -> Advice:
    owned = set(owned_ids)
    has_boots = any(_is_upgraded_boots(item(i)) for i in owned)

    def is_owned(item_id: int) -> bool:
        if item_id in owned:
            return True
        found = item(item_id)
        return found is not None and found.is_boots and has_boots

    build = tuple(BuildStep(i, is_owned(i)) for i in plan.full)

    candidates = [
        c
        for c in (
            _anti_heal(plan, report, owned, champion_name),
            _urgent_defense(plan, report, owned, item, champion_name),
        )
        if c is not None
    ]
    if candidates:
        best = max(candidates, key=lambda c: c.threat_score)  # empate: la primera (antisanación)
        chosen = (best.item_id, best.reason)
    else:
        steps = {s.item_id: s for s in build}
        next_step = next(
            (steps[i] for i in purchase_order(plan) if i in steps and not steps[i].owned), None
        )
        if next_step is None:
            return Advice(None, build, "Build completa: vende lo que no necesites o adapta.")
        reason = (
            "siguiente objeto del núcleo de tu build"
            if next_step.item_id in plan.core
            else "siguiente objeto de tu build"
        )
        if plan.boots is not None and next_step.item_id == plan.boots:
            reason = "tus botas"
        chosen = (next_step.item_id, reason)

    item_id, reason = chosen
    return Advice(_purchase(item_id, reason, owned_ids, gold, item), build)


def purchase_order(plan: BuildPlan) -> list[int]:
    """Orden de compra habitual: 1.er objeto del núcleo, botas, resto del núcleo y huecos."""
    order = list(plan.full)
    if plan.boots is not None and plan.boots in order and plan.core:
        order.remove(plan.boots)
        first = order.index(plan.core[0]) if plan.core[0] in order else -1
        order.insert(first + 1, plan.boots)
    return order


def _urgent_defense(
    plan: BuildPlan,
    report: GameReport,
    owned: set[int],
    item: ItemLookup,
    champion_name: Callable[[int], str],
) -> _Candidate | None:
    threat = next(
        (
            e
            for e in report.enemies
            if e.damage_level >= MIN_THREAT_DAMAGE
            and (
                e.strength == Strength.VERY_STRONG
                or (e.is_lane_opponent and e.strength == Strength.STRONG)
            )
        ),
        None,
    )
    if threat is None or threat.damage_type not in (DamageType.MAGIC, DamageType.PHYSICAL):
        return None
    magic = threat.damage_type == DamageType.MAGIC
    category = "SpellBlock" if magic else "Armor"
    if any(_gives(item(i), category) for i in owned):
        return None  # ya tienes defensa contra ese tipo de daño
    defensive = item_for(plan.archetype, MAGIC_DEFENSE if magic else PHYSICAL_DEFENSE)
    if defensive is None or defensive in owned or item(defensive) is None:
        return None
    kind = "mágico" if magic else "físico"
    return _Candidate(
        defensive,
        f"{_who(threat, champion_name)} va {_strength_text(threat)} ({threat.player.kda}) "
        f"y hace daño {kind}: te ayuda a sobrevivir",
        threat.score,
    )


def _anti_heal(
    plan: BuildPlan,
    report: GameReport,
    owned: set[int],
    champion_name: Callable[[int], str],
) -> _Candidate | None:
    need = next((s for s in plan.situational if s.item_id in ANTI_HEAL_ITEMS), None)
    if need is None or need.item_id in owned or need.component_id in owned:
        return None
    healer = next(
        (e for e in report.enemies if e.threatening and e.player.champion_alias in HEALERS), None
    )
    if healer is None:
        return None
    target = need.component_id if need.component_id is not None else need.item_id
    return _Candidate(
        target,
        f"{_who(healer, champion_name)} va {_strength_text(healer)} y se cura mucho: "
        "antisanación cuanto antes",
        healer.score,
    )


def _purchase(
    item_id: int, reason: str, owned_ids: tuple[int, ...], gold: float, item: ItemLookup
) -> PurchaseAdvice:
    target = item(item_id)
    if target is None:
        return PurchaseAdvice(item_id, reason, 0, False)
    inventory = Counter(owned_ids)
    cost = max(target.price_total - _owned_value(target, inventory, item), 0)
    return PurchaseAdvice(
        item_id=item_id,
        reason=reason,
        remaining_gold=max(round(cost - gold), 0),
        can_buy_now=gold >= cost,
        affordable_components=_affordable_components(target, set(owned_ids), gold, item),
    )


def _owned_value(target: Item, inventory: Counter[int], item: ItemLookup) -> int:
    """Oro de los componentes que ya tienes, en cualquier nivel de la receta."""
    value = 0
    for component_id in target.from_ids:
        component = item(component_id)
        if component is None:
            continue
        if inventory[component_id] > 0:
            inventory[component_id] -= 1
            value += component.price_total
        else:
            value += _owned_value(component, inventory, item)
    return value


def _affordable_components(
    target: Item, owned: set[int], gold: float, item: ItemLookup
) -> tuple[int, ...]:
    """Los componentes más caros que ya puedes pagar, bajando por la receta si hace falta."""
    found: dict[int, int] = {}
    pending = list(target.from_ids)
    while pending:
        component_id = pending.pop()
        component = item(component_id)
        if component is None or component_id in owned:
            continue
        if component.price_total <= gold:
            found[component_id] = component.price_total
        else:
            pending.extend(component.from_ids)
    ranked = sorted(found, key=lambda c: -found[c])
    return tuple(ranked[:MAX_COMPONENTS])


def _is_upgraded_boots(found: Item | None) -> bool:
    return found is not None and found.is_boots and found.id != BASIC_BOOTS


def _gives(found: Item | None, category: str) -> bool:
    return (
        found is not None
        and category in found.categories
        and found.price_total >= DEFENSIVE_ITEM_MIN_PRICE
        and not found.is_boots
    )


def _who(report: PlayerReport, champion_name: Callable[[int], str]) -> str:
    return champion_name(report.champion_id) if report.champion_id else report.player.champion_name


def _strength_text(report: PlayerReport) -> str:
    return "muy fuerte" if report.strength == Strength.VERY_STRONG else "fuerte"
