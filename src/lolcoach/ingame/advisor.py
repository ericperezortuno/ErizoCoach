"""¿Qué compro ahora? Sigue tu build y la adapta a cómo va la partida.

Orden de prioridad:

1. **Antisanación barata** (la única excepción al núcleo): si quien se cura mucho lo
   pide, solo su componente (Orbe del olvido, Llamada del verdugo...), no el objeto
   entero. En fase de líneas, solo si ese enemigo está en tu línea.
2. **Termina lo que empezaste**: si ya tienes componentes de un objeto, se acaba ese
   antes de pasar a otro (las botas básicas y el componente de antisanación no cuentan).
3. **El núcleo de tu build** (con las botas tras el primer objeto), sin desvíos.
4. Con el núcleo completo, **respuesta a la mayor amenaza**:
   * Defensa urgente si un enemigo que hace mucho daño va muy fuerte (o tu rival de
     línea va fuerte) y aún no tienes defensa contra su tipo de daño.
   * Antisanación completa si quien se cura mucho va fuerte y aún no la tienes.
   Si se dan las dos, va primero la que responde al enemigo más fuerte (en empate,
   la antisanación). En fase de líneas solo cuentan los enemigos de tu línea. Un aviso
   de defensa se mantiene mientras ese enemigo siga yendo fuerte (``keep_threat``): así
   no alterna con tu build cada pocos segundos cuando su puntuación ronda el límite.
5. **El resto de tu build**, en orden.

Además calcula cuánto oro te falta (descontando los componentes que ya tienes,
en cualquier nivel de la receta) y qué componentes puedes comprar ya.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..builds.models import BuildPlan, SituationalItem
from ..builds.rules import (
    ANTI_HEAL_COMPONENTS,
    ANTI_HEAL_ITEMS,
    HEALERS,
    MAGIC_DEFENSE,
    PHYSICAL_DEFENSE,
    STRONG_HEALERS,
    item_for,
)
from ..gamedata.catalog import DamageType
from ..gamedata.items import Item
from .analysis import GameReport, PlayerReport, Strength

BASIC_BOOTS = 1001
LANING_PHASE_END = 14 * 60  # a los 14:00 caen las placas de las torres
DEFENSIVE_ITEM_MIN_PRICE = 1000  # un componente pequeño no cuenta como "ya tengo defensa"
MIN_THREAT_DAMAGE = 2  # nivel de daño (0-3) a partir del que compensa defenderse
MAX_COMPONENTS = 3
# Se compran sueltos a propósito: no cuentan como «objeto empezado»
_LOOSE_COMPONENTS = frozenset({BASIC_BOOTS, *(c for c in ANTI_HEAL_COMPONENTS.values() if c)})
STARTED = "termina el objeto que has empezado"


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
    threat_id: int = 0  # campeón al que responde la compra (0 = sigue tu build)


ItemLookup = Callable[[int], Item | None]


@dataclass(frozen=True, slots=True)
class _Candidate:
    item_id: int
    reason: str
    threat_score: float
    threat_id: int


def advise(
    plan: BuildPlan,
    report: GameReport,
    owned_ids: tuple[int, ...],
    gold: float,
    item: ItemLookup,
    champion_name: Callable[[int], str],
    *,
    game_time: float,
    boots_out_of_sight: bool = False,
    keep_threat: int = 0,
) -> Advice:
    """``game_time``: segundos de partida (decide si sigue la fase de líneas).
    ``boots_out_of_sight``: tienes botas que la API no muestra (espacio de la misión de rol).
    ``keep_threat``: campeón al que respondía el consejo anterior (``Advice.threat_id``)."""
    owned = set(owned_ids)
    has_boots = boots_out_of_sight or any(_is_upgraded_boots(item(i)) for i in owned)
    laning = game_time < LANING_PHASE_END

    def is_owned(item_id: int) -> bool:
        if item_id in owned:
            return True
        found = item(item_id)
        return found is not None and found.is_boots and has_boots

    build = tuple(BuildStep(i, is_owned(i)) for i in plan.full)
    pending = [i for i in purchase_order(plan) if not is_owned(i)]
    anti_heal = next((s for s in plan.situational if s.item_id in ANTI_HEAL_ITEMS), None)

    def chosen(item_id: int, reason: str, threat_id: int = 0) -> Advice:
        purchase = _purchase(item_id, reason, owned_ids, gold, item)
        return Advice(purchase, build, threat_id=threat_id)

    core_done = all(is_owned(i) for i in plan.core)

    # 1) Antisanación barata: la única compra que se adelanta al núcleo
    if not core_done:
        cheap = _anti_heal_component(anti_heal, report, owned, champion_name, laning=laning)
        if cheap is not None:
            return chosen(cheap.item_id, cheap.reason, cheap.threat_id)

    # 2) Lo que ya has empezado se termina (también la defensa de un aviso anterior)
    defenses = [
        d
        for d in (
            item_for(plan.archetype, MAGIC_DEFENSE),
            item_for(plan.archetype, PHYSICAL_DEFENSE),
        )
        if d is not None and d not in owned
    ]
    started = _started([*pending, *(d for d in defenses if d not in pending)], owned_ids, item)
    if started is not None:
        return chosen(started, STARTED, keep_threat if started in defenses else 0)

    # 3) El núcleo, sin desvíos
    if not core_done:
        return chosen(pending[0], _build_reason(plan, pending[0]))

    # 4) Con el núcleo hecho, la respuesta a la mayor amenaza
    candidates = [
        c
        for c in (
            _anti_heal_item(anti_heal, report, owned, champion_name, laning=laning),
            _urgent_defense(plan, report, owned, item, champion_name, keep_threat, laning=laning),
        )
        if c is not None
    ]
    if candidates:
        best = max(candidates, key=lambda c: c.threat_score)  # empate: la primera (antisanación)
        return chosen(best.item_id, best.reason, best.threat_id)

    # 5) El resto de la build
    if not pending:
        return Advice(None, build, "Build completa: vende lo que no necesites o adapta.")
    return chosen(pending[0], _build_reason(plan, pending[0]))


def purchase_order(plan: BuildPlan) -> list[int]:
    """Orden de compra habitual: 1.er objeto del núcleo, botas, resto del núcleo y huecos."""
    order = list(plan.full)
    if plan.boots is not None and plan.boots in order and plan.core:
        order.remove(plan.boots)
        first = order.index(plan.core[0]) if plan.core[0] in order else -1
        order.insert(first + 1, plan.boots)
    return order


def _build_reason(plan: BuildPlan, item_id: int) -> str:
    if plan.boots is not None and item_id == plan.boots:
        return "tus botas"
    if item_id in plan.core:
        return "siguiente objeto del núcleo de tu build"
    return "siguiente objeto de tu build"


def _started(candidates: Iterable[int], owned_ids: tuple[int, ...], item: ItemLookup) -> int | None:
    """El primer objeto (en orden de compra) del que ya tienes algún componente."""
    loose = Counter(i for i in owned_ids if i not in _LOOSE_COMPONENTS)
    if not loose:
        return None
    for item_id in candidates:
        target = item(item_id)
        if target is not None and _owned_value(target, loose.copy(), item) > 0:
            return item_id
    return None


def _urgent_defense(
    plan: BuildPlan,
    report: GameReport,
    owned: set[int],
    item: ItemLookup,
    champion_name: Callable[[int], str],
    keep_threat: int = 0,
    *,
    laning: bool = False,
) -> _Candidate | None:
    threat = next(
        (
            e
            for e in report.enemies
            if e.damage_level >= MIN_THREAT_DAMAGE
            and (e.in_lane or not laning)  # en fase de líneas, solo los de tu línea
            and (
                e.strength == Strength.VERY_STRONG
                or (e.threatening and (e.is_lane_opponent or e.champion_id == keep_threat))
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
        threat.champion_id,
    )


def _anti_heal_component(
    need: SituationalItem | None,
    report: GameReport,
    owned: set[int],
    champion_name: Callable[[int], str],
    *,
    laning: bool,
) -> _Candidate | None:
    """El componente de antisanación, antes incluso que el núcleo.

    En fase de líneas: si quien se cura está en tu línea y es de los que se curan
    muchísimo (Aatrox, Soraka...) o va fuerte. Después: si va fuerte."""
    if need is None or need.component_id is None:
        return None
    if need.item_id in owned or need.component_id in owned:
        return None

    def wanted(e: PlayerReport) -> bool:
        if e.player.champion_alias not in HEALERS:
            return False
        if not laning:
            return e.threatening
        return e.in_lane and (e.threatening or e.player.champion_alias in STRONG_HEALERS)

    healer = next((e for e in report.enemies if wanted(e)), None)
    if healer is None:
        return None
    state = f"va {_strength_text(healer)}" if healer.threatening else "está"
    where = " en tu línea" if laning else ""
    return _Candidate(
        need.component_id,
        f"{_who(healer, champion_name)} {state}{where} y se cura mucho: "
        "compra ya el componente de antisanación (el objeto entero, más adelante)",
        healer.score,
        healer.champion_id,
    )


def _anti_heal_item(
    need: SituationalItem | None,
    report: GameReport,
    owned: set[int],
    champion_name: Callable[[int], str],
    *,
    laning: bool,
) -> _Candidate | None:
    """El objeto de antisanación completo: solo con el núcleo hecho."""
    if need is None or need.item_id in owned:
        return None
    healer = next(
        (
            e
            for e in report.enemies
            if e.threatening and e.player.champion_alias in HEALERS and (e.in_lane or not laning)
        ),
        None,
    )
    if healer is None:
        return None
    return _Candidate(
        need.item_id,
        f"{_who(healer, champion_name)} va {_strength_text(healer)} y se cura mucho: "
        "completa la antisanación",
        healer.score,
        healer.champion_id,
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
