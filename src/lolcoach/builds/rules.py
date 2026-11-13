"""Adaptación de la build a la composición enemiga.

Cómo funciona:

1. Se detectan las **necesidades** frente a este equipo (antisanación, resistencia
   mágica, armadura, limpiar control, protegerse de asesinos, penetrar tanques,
   romper escudos), por orden de prioridad.
2. Cada necesidad se traduce en el objeto adecuado **para tu tipo de campeón**
   (un mago no compra lo mismo que un tanque contra la curación).
3. El núcleo de la build no se toca. Las necesidades más importantes ocupan los
   huecos 4.º y 5.º; las demás quedan en el bloque «Contra este equipo».
4. Los campeones de primera línea cambian también las botas.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..gamedata.catalog import ChampionProfile, DamageType
from ..loadout.models import HARD_CC_CHAMPIONS, HEAVY_CC_TOTAL
from ..models import Position
from .archetypes import AP_DAMAGE, FRONTLINE, MARKSMEN, MERCURY, PLATED
from .models import Archetype, BaseBuild, BuildPlan, SituationalItem

# Campeones (clave interna del cliente) para reglas que el perfil no refleja
HEALERS = frozenset({
    "Soraka", "Aatrox", "Vladimir", "Warwick", "DrMundo", "Sylas", "Swain", "Yuumi", "Briar",
    "Illaoi", "Volibear", "Trundle", "Fiora", "Gwen", "Belveth", "Nasus", "Olaf", "Viego",
    "FiddleSticks", "Nami", "Sona", "Zac",
})  # fmt: skip
STRONG_HEALERS = frozenset(
    {"Soraka", "Aatrox", "Vladimir", "Warwick", "DrMundo", "Yuumi", "Briar", "Swain"}
)
SUPPRESSORS = frozenset({"Malzahar", "Skarner", "Warwick", "Urgot"})
SHIELDERS = frozenset({
    "Lulu", "Janna", "Karma", "Milio", "Orianna", "Ivern", "Taric", "Seraphine", "Sona", "Renata",
})  # fmt: skip

THREAT_COUNT = 3  # enemigos con un tipo de daño para que compense defenderse de él
TANK_COUNT = 2
DIVE_COUNT = 2
SHIELD_COUNT = 2
FLEX_SLOTS = 2  # huecos de la build completa que se adaptan a los enemigos

# Componentes baratos
OBLIVION_ORB, EXECUTIONERS, BRAMBLE_VEST, QUICKSILVER = 3916, 3123, 3076, 3140


class Names(Protocol):
    def champion_name(self, champion_id: int) -> str: ...
    def item_name(self, item_id: int) -> str: ...
    def is_buyable(self, item_id: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class Enemy:
    champion_id: int
    alias: str
    profile: ChampionProfile | None


@dataclass(frozen=True, slots=True)
class BuildContext:
    archetype: Archetype
    position: Position
    map_id: int
    enemies: tuple[Enemy, ...] = ()

    @property
    def known(self) -> bool:
        return bool(self.enemies)

    def _profiles(self) -> list[ChampionProfile]:
        return [e.profile for e in self.enemies if e.profile is not None]

    def _threat(self, damage_type: DamageType) -> float:
        total = 0.0
        for p in self._profiles():
            if p.damage < 2:
                continue
            if p.damage_type == damage_type:
                total += 1
            elif p.damage_type == DamageType.MIXED:
                total += 0.5
        return total

    @property
    def magic_threats(self) -> float:
        return self._threat(DamageType.MAGIC)

    @property
    def physical_threats(self) -> float:
        return self._threat(DamageType.PHYSICAL)

    @property
    def physical_carries(self) -> int:
        """Tiradores y asesinos físicos: los que hacen que la armadura compense."""
        return sum(
            p.damage_type == DamageType.PHYSICAL
            and ("marksman" in p.roles or "assassin" in p.roles)
            for p in self._profiles()
        )

    def named(self, aliases: frozenset[str]) -> list[Enemy]:
        return [e for e in self.enemies if e.alias in aliases]

    @property
    def needs_anti_heal(self) -> bool:
        return bool(self.named(STRONG_HEALERS)) or len(self.named(HEALERS)) >= 2

    @property
    def tanks(self) -> int:
        return sum(p.durability >= 3 or (p.roles[:1] == ("tank",)) for p in self._profiles())

    @property
    def dive_threats(self) -> int:
        return sum(p.is_dive_threat for p in self._profiles())

    @property
    def heavy_cc(self) -> bool:
        profiles = self._profiles()
        hard = sum(p.crowd_control >= 3 for p in profiles)
        return hard >= HARD_CC_CHAMPIONS or sum(p.crowd_control for p in profiles) >= HEAVY_CC_TOTAL


# Objeto según el grupo de tu campeón (reutilizado por el consejero de la fase 4)
MAGIC_DEFENSE: dict[str, int | None] = {
    "ap": 3102, "marksman": 3139, "assassin": 3156, "fighter": 3065, "tank": 2504,
}  # Velo del hada, Cimitarra mercurial, Fauces, Rostro espiritual, Rookern kaénico  # fmt: skip
PHYSICAL_DEFENSE: dict[str, int | None] = {
    "ap": 3157, "marksman": 3026, "ad_melee": 6333, "tank": 3143, "support_tank": 3110,
}  # Zhonya, Ángel de la guarda, Baile de la muerte, Randuin, Corazón de hielo  # fmt: skip
# Morellonomicón, Recordatorio letal, Mecanoespada punki, Malla de espinas
ANTI_HEAL_ITEMS = frozenset({3165, 3033, 6609, 3075})
ANTI_HEAL_COMPONENTS: dict[str, int | None] = {
    "ap": OBLIVION_ORB, "marksman": EXECUTIONERS, "ad_melee": EXECUTIONERS, "tank": BRAMBLE_VEST,
}  # fmt: skip


def item_for(archetype: Archetype, table: dict[str, int | None]) -> int | None:
    """El objeto de ``table`` que corresponde a tu tipo de campeón (None si no aplica)."""
    return _group_item(archetype, table)


def _group_item(archetype: Archetype, table: dict[str, int | None]) -> int | None:
    if archetype in AP_DAMAGE:
        key = "ap_fighter" if archetype == Archetype.AP_FIGHTER and "ap_fighter" in table else "ap"
    elif archetype in MARKSMEN:
        key = "marksman"
    elif archetype == Archetype.ASSASSIN_AD:
        key = "assassin" if "assassin" in table else "ad_melee"
    elif archetype == Archetype.FIGHTER_AD:
        key = "fighter" if "fighter" in table else "ad_melee"
    elif archetype == Archetype.TANK:
        key = "tank"
    elif archetype == Archetype.SUPPORT_TANK:
        key = "support_tank" if "support_tank" in table else "tank"
    else:
        key = "enchanter"
    return table.get(key)


def find_needs(ctx: BuildContext, names: Names) -> list[SituationalItem]:
    """Objetos que este equipo enemigo pide, de más a menos importante."""
    if not ctx.known:
        return []
    needs: list[SituationalItem] = []
    a = ctx.archetype

    def add(table: dict[str, int | None], reason: Callable[[str], str], component: dict) -> None:
        item = _group_item(a, table)
        if item is None or not names.is_buyable(item) or any(n.item_id == item for n in needs):
            return
        comp = _group_item(a, component) if component else None
        if comp is not None and not names.is_buyable(comp):
            comp = None
        needs.append(SituationalItem(item, reason(names.item_name(item)), comp))

    def who(enemies: list[Enemy]) -> str:
        return ", ".join(names.champion_name(e.champion_id) for e in enemies)

    def verb(enemies: list[Enemy], singular: str, plural: str) -> str:
        return singular if len(enemies) == 1 else plural

    # 1) Antisanación. La de los tanques (Malla de espinas) da armadura: solo compensa
    #    si alguno de los que se curan hace daño físico.
    if ctx.needs_anti_heal:
        healers = ctx.named(HEALERS)
        physical_healer = any(
            e.profile is not None and e.profile.damage_type != DamageType.MAGIC for e in healers
        )
        tank_item = 3075 if physical_healer else None
        add(
            {"ap": 3165, "marksman": 3033, "ad_melee": 6609, "tank": tank_item},
            lambda item: f"{item}: {who(healers)} {verb(healers, 'se cura', 'se curan')} mucho",
            ANTI_HEAL_COMPONENTS,
        )

    # 2) Supresiones: hay que poder liberarse
    suppressors = ctx.named(SUPPRESSORS)
    if suppressors:
        add(
            {"marksman": 3139, "enchanter": 3222},
            lambda item: (
                f"{item}: {who(suppressors)} te {verb(suppressors, 'puede', 'pueden')} suprimir"
            ),
            {"marksman": QUICKSILVER},
        )

    # 3) Defensa según de dónde venga el daño enemigo (primero el más peligroso)
    magic, physical = ctx.magic_threats, ctx.physical_threats
    defenses: list[tuple[float, Callable[[], None]]] = []
    if magic >= THREAT_COUNT:
        defenses.append((magic, lambda: add(
            MAGIC_DEFENSE, lambda item: f"{item}: {magic:g} enemigos hacen daño mágico", {},
        )))  # fmt: skip
    if physical >= THREAT_COUNT and ctx.physical_carries >= 1:
        defenses.append((physical, lambda: add(
            PHYSICAL_DEFENSE, lambda item: f"{item}: {physical:g} enemigos hacen daño físico", {},
        )))  # fmt: skip
    for _, apply in sorted(defenses, key=lambda d: -d[0]):
        apply()

    # 4) Asesinos que se lanzan sobre ti
    if ctx.dive_threats >= DIVE_COUNT and a not in FRONTLINE:
        add(
            {"ap": 3157, "marksman": 6673, "enchanter": 3222},
            lambda item: f"{item}: {ctx.dive_threats} asesinos pueden lanzarse sobre ti",
            {},
        )

    # 5) Mucho control (para quien no lo resuelve con las botas)
    if ctx.heavy_cc and a in MARKSMEN | {Archetype.SUPPORT_ENCHANTER}:
        add(
            {"marksman": 3139, "enchanter": 3222},
            lambda item: f"{item}: el equipo enemigo tiene mucho control",
            {"marksman": QUICKSILVER},
        )

    # 6) Varios tanques
    if ctx.tanks >= TANK_COUNT:
        add(
            {"ap": 3135, "marksman": 3036, "ad_melee": 3071},
            lambda item: f"{item}: hay {ctx.tanks} tanques en el equipo enemigo",
            {},
        )

    # 7) Muchos escudos
    shielders = ctx.named(SHIELDERS)
    if len(shielders) >= SHIELD_COUNT:
        add(
            {"ad_melee": 6695},
            lambda item: f"{item}: {who(shielders)} dan muchos escudos",  # siempre 2 o más
            {},
        )
    return needs


def adapt_build(base: BaseBuild, ctx: BuildContext, champion_id: int, names: Names) -> BuildPlan:
    adjustments: list[str] = []
    needs = find_needs(ctx, names)

    boots = base.boots
    if ctx.known and boots is not None and ctx.archetype in FRONTLINE:
        if ctx.magic_threats >= THREAT_COUNT or ctx.heavy_cc or ctx.named(SUPPRESSORS):
            new_boots, why = MERCURY, "mucho daño mágico o control enemigo"
        elif ctx.physical_threats >= THREAT_COUNT:
            new_boots, why = PLATED, "mucho daño físico enemigo"
        else:
            new_boots, why = boots, ""
        if new_boots != boots:
            adjustments.append(f"{names.item_name(new_boots)}: {why}")
            boots = new_boots

    flex = [n for n in needs if n.item_id not in base.core][:FLEX_SLOTS]
    adjustments.extend(n.reason for n in flex)
    full = list(base.core) + [n.item_id for n in flex]
    target = 6 if boots is None else 5
    for item in base.extra:
        if len(full) >= target:
            break
        if item not in full:
            full.append(item)
    if boots is not None:
        full.insert(0, boots)

    return BuildPlan(
        champion_id=champion_id,
        position=ctx.position,
        map_id=ctx.map_id,
        archetype=base.archetype,
        source=base.source,
        starting=base.starting,
        boots=boots,
        core=base.core,
        full=tuple(full),
        situational=tuple(needs),
        support_upgrade=base.support_upgrade,
        adjustments=tuple(adjustments),
    )
