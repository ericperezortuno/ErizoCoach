"""Builds base por tipo de campeón ("arquetipo"), validadas contra el parche actual.

Riot no ofrece builds recomendadas en el cliente, así que la base sale de estas
reglas. Es una fuente provisional: ``BuildSource`` permite sustituirla (o
combinarla) más adelante por estadísticas reales de partidas de alto elo.

Cada objeto se comprueba en tiempo de ejecución contra ``items.json`` del
parche: si Riot quita uno, se descarta y la build se completa con los siguientes.
"""

from __future__ import annotations

from typing import Protocol

from ..gamedata.catalog import Champion, ChampionProfile, DamageType
from ..models import Position
from .models import Archetype, BaseBuild, ItemCount

SUMMONERS_RIFT, HOWLING_ABYSS = 11, 12
SOURCE_RULES = "reglas"

# --- Objetos (ids estables; nombre en el cliente en español) ------------------------------
POTION = 2003  # Poción de vida
DORAN_RING, DORAN_BLADE, DORAN_SHIELD = 1056, 1055, 1054
WORLD_ATLAS = 3865  # Atlas mundial (objeto de apoyo)
SCORCHCLAW, MOSSTOMPER = 1101, 1103  # Cachorro de Garra ígnea, Brote de Brincamontes
GUARDIAN_ORB, GUARDIAN_BLADE, GUARDIAN_HAMMER, GUARDIAN_HORN = 3112, 3177, 3184, 2051  # ARAM

SORCERER, BERSERKER, PLATED, MERCURY, IONIAN, SWIFTNESS = 3020, 3006, 3047, 3111, 3158, 3009

ARCHETYPE_ITEMS: dict[Archetype, tuple[int | None, tuple[int, ...], tuple[int, ...]]] = {
    # arquetipo: (botas, núcleo en orden, siguientes)
    Archetype.MAGE_BURST: (
        SORCERER,
        (6655, 4645, 3089),  # Eco de Luden, Llamasombría, Sombrero mortal de Rabadon
        (3135, 3157, 3102),  # Bastón del Vacío, Reloj de arena de Zhonya, Velo del hada
    ),
    Archetype.MAGE_SUSTAINED: (
        SORCERER,
        (6653, 3116, 3089),  # Tormento de Liandry, Cetro de cristal de Rylai, Rabadon
        (3135, 3157, 3165),  # Bastón del Vacío, Zhonya, Morellonomicón
    ),
    Archetype.AP_FIGHTER: (
        SORCERER,
        (4633, 6653, 3157),  # Creagrietas, Tormento de Liandry, Zhonya
        (3089, 3135, 3102),  # Rabadon, Bastón del Vacío, Velo del hada
    ),
    Archetype.MARKSMAN_CRIT: (
        BERSERKER,
        (3032, 3031, 3046),  # Flechas de los Yun Tal, Filo infinito, Bailarín espectral
        (3036, 3072, 3026),  # Recuerdos de lord Dominik, Sanguinaria, Ángel de la guarda
    ),
    Archetype.MARKSMAN_ONHIT: (
        BERSERKER,
        (3153, 3124, 3091),  # Hoja del rey arruinado, Hoja de furia de Guinsoo, Final del ingenio
        (3302, 3046, 3026),  # El final, Bailarín espectral, Ángel de la guarda
    ),
    Archetype.ASSASSIN_AD: (
        IONIAN,
        (3142, 6697, 6694),  # Filo fantasmal de Youmuu, Soberbia, Rencor de Serylda
        (3814, 6696, 3156),  # Filo de la noche, Arco axiomático, Fauces de Malmortius
    ),
    Archetype.FIGHTER_AD: (
        PLATED,
        (3078, 3053, 3071),  # Fuerza de trinidad, Calibrador de Sterak, Cuchilla negra
        (6333, 3065, 3026),  # Baile de la muerte, Rostro espiritual, Ángel de la guarda
    ),
    Archetype.TANK: (
        PLATED,
        (3068, 6665, 3075),  # Égida de fuego solar, Jak'Sho el Proteico, Malla de espinas
        (2504, 3143, 3083),  # Rookern kaénico, Presagio de Randuin, Armadura de Warmog
    ),
    Archetype.SUPPORT_TANK: (
        PLATED,
        (3190, 3109, 3050),  # Medallón de los Solari, Promesa de caballero, Convergencia de Zeke
        (3107, 3075, 2504),  # Redención, Malla de espinas, Rookern kaénico
    ),
    Archetype.SUPPORT_ENCHANTER: (
        IONIAN,
        (6617, 6616, 3107),  # Renovación de piedra lunar, Bastón de aguas fluidas, Redención
        (3504, 3222, 6620),  # Incensario ardiente, Bendición de Mikael, Ecos de Helia
    ),
}

# Mejora del Atlas mundial según el tipo de apoyo
SUPPORT_UPGRADES = {
    Archetype.SUPPORT_TANK: 3869,  # Detracción celestial
    Archetype.SUPPORT_ENCHANTER: 3870,  # Tejesueños
}
MAGE_SUPPORT_UPGRADE = 3871  # Perforaplanos de Zaz'Zak

FRONTLINE = frozenset(
    {Archetype.AP_FIGHTER, Archetype.FIGHTER_AD, Archetype.TANK, Archetype.SUPPORT_TANK}
)
AP_DAMAGE = frozenset({Archetype.MAGE_BURST, Archetype.MAGE_SUSTAINED, Archetype.AP_FIGHTER})
MARKSMEN = frozenset({Archetype.MARKSMAN_CRIT, Archetype.MARKSMAN_ONHIT})

# --- Excepciones por campeón (clave interna estable del cliente) ----------------------------
ARCHETYPE_OVERRIDES: dict[str, Archetype] = {
    # Tiradores de efectos al impacto
    "Vayne": Archetype.MARKSMAN_ONHIT,
    "KogMaw": Archetype.MARKSMAN_ONHIT,
    "Kalista": Archetype.MARKSMAN_ONHIT,
    "Twitch": Archetype.MARKSMAN_ONHIT,
    "Varus": Archetype.MARKSMAN_ONHIT,
    "Kaisa": Archetype.MARKSMAN_ONHIT,  # híbrida (con objetos propios abajo)
    # Magos de daño continuado
    "Brand": Archetype.MAGE_SUSTAINED,
    "Cassiopeia": Archetype.MAGE_SUSTAINED,
    "Malzahar": Archetype.MAGE_SUSTAINED,
    "Ryze": Archetype.MAGE_SUSTAINED,
    "Swain": Archetype.MAGE_SUSTAINED,
    "Karthus": Archetype.MAGE_SUSTAINED,
    "Anivia": Archetype.MAGE_SUSTAINED,
    "Zyra": Archetype.MAGE_SUSTAINED,
    "Heimerdinger": Archetype.MAGE_SUSTAINED,
    # Luchadores / tanques de poder de habilidad
    "Vladimir": Archetype.AP_FIGHTER,
    "Galio": Archetype.AP_FIGHTER,
    "Gwen": Archetype.AP_FIGHTER,
    "Rumble": Archetype.AP_FIGHTER,
    # Apoyos que construyen daño físico
    "Pyke": Archetype.ASSASSIN_AD,
}

# Campeones con objetos propios (el resto de la build sale de su arquetipo)
ITEM_OVERRIDES: dict[str, dict[str, object]] = {
    "Smolder": {"core": (3508, 6675, 3031)},  # Segador de esencia, Navori, Filo infinito
    "Kaisa": {"core": (6672, 3115, 3124), "extra": (3089, 3157, 3026)},  # híbrida
    "Ezreal": {"core": (3004, 3078, 6694), "extra": (3036, 3026, 3071), "boots": IONIAN},
    "Jhin": {"core": (6676, 3031, 3094), "boots": SWIFTNESS},  # Recaudadora, Filo, Cañón
    "Cassiopeia": {"boots": None},  # no puede comprar botas
    # Espadachines de críticos (no Fuerza de trinidad): Arcoescudo, Filo infinito, Baile
    "Yasuo": {"core": (6673, 3031, 6333), "extra": (3026, 3139, 3072), "boots": BERSERKER},
    "Yone": {"core": (6673, 3031, 6333), "extra": (3026, 3139, 3072), "boots": BERSERKER},
}


class BuildSource(Protocol):
    """De dónde sale la build base (reglas hoy; estadísticas reales en el futuro)."""

    def base_build(
        self,
        champion: Champion | None,
        profile: ChampionProfile | None,
        position: Position,
        map_id: int,
    ) -> BaseBuild: ...


def classify(
    champion: Champion | None, profile: ChampionProfile | None, position: Position
) -> Archetype:
    alias = champion.alias if champion else ""
    if alias in ARCHETYPE_OVERRIDES:
        return ARCHETYPE_OVERRIDES[alias]
    roles = (profile.roles if profile and profile.roles else None) or (
        champion.roles if champion else ()
    )
    primary = roles[0] if roles else ""
    magic = profile is not None and profile.damage_type == DamageType.MAGIC

    if position == Position.UTILITY:
        if primary == "tank" or (primary == "support" and "tank" in roles):
            return Archetype.SUPPORT_TANK
        if primary == "support":
            return Archetype.SUPPORT_ENCHANTER
    if primary == "marksman":
        return Archetype.MARKSMAN_CRIT
    if primary == "mage":
        return Archetype.MAGE_BURST
    if primary == "assassin":
        return Archetype.MAGE_BURST if magic else Archetype.ASSASSIN_AD
    if primary == "tank":
        return Archetype.TANK
    if primary == "fighter":
        return Archetype.AP_FIGHTER if magic else Archetype.FIGHTER_AD
    if primary == "support":
        return Archetype.SUPPORT_TANK if "tank" in roles else Archetype.SUPPORT_ENCHANTER
    return Archetype.MAGE_BURST if magic else Archetype.FIGHTER_AD


def starting_items(archetype: Archetype, position: Position, map_id: int) -> tuple[ItemCount, ...]:
    if map_id == HOWLING_ABYSS:
        if archetype in MARKSMEN or archetype == Archetype.ASSASSIN_AD:
            guardian = GUARDIAN_BLADE
        elif archetype == Archetype.FIGHTER_AD:
            guardian = GUARDIAN_HAMMER
        elif archetype in (Archetype.TANK, Archetype.SUPPORT_TANK):
            guardian = GUARDIAN_HORN
        else:
            guardian = GUARDIAN_ORB
        return (ItemCount(guardian),)
    if position == Position.JUNGLE:
        pet = MOSSTOMPER if archetype in (Archetype.TANK, Archetype.SUPPORT_TANK) else SCORCHCLAW
        return (ItemCount(pet), ItemCount(POTION))
    if position == Position.UTILITY:
        return (ItemCount(WORLD_ATLAS), ItemCount(POTION, 2))
    if archetype in AP_DAMAGE or archetype == Archetype.SUPPORT_ENCHANTER:
        return (ItemCount(DORAN_RING), ItemCount(POTION, 2))
    if archetype in (Archetype.TANK, Archetype.SUPPORT_TANK):
        return (ItemCount(DORAN_SHIELD), ItemCount(POTION))
    return (ItemCount(DORAN_BLADE), ItemCount(POTION))


class RuleBasedSource:
    """Build base por arquetipo, con excepciones para campeones especiales."""

    def base_build(
        self,
        champion: Champion | None,
        profile: ChampionProfile | None,
        position: Position,
        map_id: int,
    ) -> BaseBuild:
        archetype = classify(champion, profile, position)
        boots, core, extra = ARCHETYPE_ITEMS[archetype]
        override = ITEM_OVERRIDES.get(champion.alias if champion else "", {})
        upgrade = None
        if position == Position.UTILITY and map_id == SUMMONERS_RIFT:
            upgrade = SUPPORT_UPGRADES.get(
                archetype, MAGE_SUPPORT_UPGRADE if archetype in AP_DAMAGE else None
            )
        return BaseBuild(
            archetype=archetype,
            source=SOURCE_RULES,
            starting=starting_items(archetype, position, map_id),
            boots=override.get("boots", boots),  # type: ignore[arg-type]
            core=override.get("core", core),  # type: ignore[arg-type]
            extra=override.get("extra", extra),  # type: ignore[arg-type]
            support_upgrade=upgrade,
        )
