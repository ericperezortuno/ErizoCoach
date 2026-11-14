"""Ajustes sobre la recomendación de Riot según la partida.

Principios:

* **Conservadores**: la base es la recomendación oficial de Riot para tu campeón
  y posición; solo se cambia algo cuando hay un motivo claro.
* **Explicables**: cada ajuste devuelve una frase con el porqué.
* **Siempre válidos**: las runas solo se cambian por otras de la misma fila, y
  los hechizos solo por otros disponibles en el modo de juego.
* **Sin enemigos conocidos** (selección a ciegas) **no se ajusta nada**: se usa la
  recomendación por defecto para tu campeón.
* **Aplastar, siempre en la Grieta**: obligatorio yendo a la jungla y prohibido en
  cualquier otra posición (``smite_rule``), se vean o no los rivales.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from ..gamedata.catalog import SummonerSpell
from ..gamedata.runes import PRIMARY_INDEXES, SECONDARY_INDEXES, SHARD_INDEXES, RunePage
from ..models import Position
from .models import DIVE_THREATS, MatchContext

# Hechizos de invocador (IDs estables del juego)
CLEANSE, EXHAUST, FLASH, HEAL, SMITE, TELEPORT, IGNITE, BARRIER = 1, 3, 4, 7, 11, 12, 14, 21

# Fragmentos de la 3.ª fila (defensa)
SHARD_HEALTH, SHARD_TENACITY = 5011, 5013
DEFENSE_SHARD_INDEX = SHARD_INDEXES[2]

# Fila 2 del árbol Valor: Condicionamiento, Fuerzas renovadas, Revestimiento de huesos
CONDITIONING, SECOND_WIND, BONE_PLATING = 8429, 8444, 8473
RESOLVE_ROW_2 = frozenset({CONDITIONING, SECOND_WIND, BONE_PLATING})


SUMMONERS_RIFT = 11
# Sin Aplastar fuera de la jungla: lo que se lleva en su lugar en cada posición, por orden
INSTEAD_OF_SMITE = {
    Position.TOP: (TELEPORT, IGNITE),
    Position.MIDDLE: (IGNITE, TELEPORT),
    Position.BOTTOM: (HEAL, BARRIER),
    Position.UTILITY: (IGNITE, EXHAUST),
}


class SmiteRule(StrEnum):
    FREE = ""  # sin posición (a ciegas, práctica) o fuera de la Grieta: lo decides tú
    REQUIRED = "required"  # en la jungla: Aplastar en la D o en la F
    FORBIDDEN = "forbidden"  # en cualquier otra posición: sin Aplastar


def smite_rule(position: Position, map_id: int) -> SmiteRule:
    if map_id != SUMMONERS_RIFT or position == Position.NONE:
        return SmiteRule.FREE
    return SmiteRule.REQUIRED if position == Position.JUNGLE else SmiteRule.FORBIDDEN


def smite_problem(spells: tuple[int, int], rule: SmiteRule) -> str:
    """Por qué esos hechizos no valen con esa regla ("" si valen)."""
    if rule == SmiteRule.REQUIRED and SMITE not in spells:
        return "En la jungla Aplastar es obligatorio: tiene que ir en la D o en la F."
    if rule == SmiteRule.FORBIDDEN and SMITE in spells:
        return "Aplastar solo se puede llevar yendo a la jungla."
    return ""


class Names(Protocol):
    def champion_name(self, champion_id: int) -> str: ...
    def perk_name(self, perk_id: int) -> str: ...
    def spell_name(self, spell_id: int) -> str: ...
    def spell(self, spell_id: int) -> SummonerSpell | None: ...


# --- Runas ---------------------------------------------------------------------


def adjust_runes(page: RunePage, ctx: MatchContext, names: Names) -> tuple[RunePage, list[str]]:
    if not ctx.enemies_known:
        return page, []
    reasons: list[str] = []
    opponent = ctx.confident_lane_opponent

    # 1) Fragmento defensivo
    shard = page.perk_ids[DEFENSE_SHARD_INDEX]
    if ctx.heavy_cc:
        if shard != SHARD_TENACITY:
            page = page.with_perk(DEFENSE_SHARD_INDEX, SHARD_TENACITY)
            reasons.append(
                f"{names.perk_name(SHARD_TENACITY)}: el equipo enemigo tiene mucho control "
                f"({ctx.hard_cc_count} campeones con control fuerte)"
            )
    elif (
        opponent is not None
        and opponent.is_burst_threat
        and ctx.me is not None
        and ctx.me.is_squishy
        and shard != SHARD_HEALTH
    ):
        page = page.with_perk(DEFENSE_SHARD_INDEX, SHARD_HEALTH)
        reasons.append(
            f"{names.perk_name(SHARD_HEALTH)}: tu rival de línea "
            f"({names.champion_name(opponent.id)}) tiene mucho daño explosivo"
        )

    # 2) Fila 2 de Valor, solo si la página ya la lleva (no se cambia de árbol)
    if opponent is not None:
        if opponent.is_burst_threat:
            target, why = BONE_PLATING, "reduce el daño de sus combos"
        elif opponent.is_ranged_poke:
            target, why = SECOND_WIND, "recuperas vida cuando te castiga desde lejos"
        else:
            target, why = 0, ""
        if target and target not in page.perk_ids:
            index = next(
                (
                    i
                    for i in (*PRIMARY_INDEXES, *SECONDARY_INDEXES)
                    if page.perk_ids[i] in RESOLVE_ROW_2
                ),
                None,
            )
            if index is not None:
                page = page.with_perk(index, target)
                reasons.append(
                    f"{names.perk_name(target)}: contra {names.champion_name(opponent.id)}, {why}"
                )
    return page, reasons


# --- Hechizos ----------------------------------------------------------------------


def adjust_spells(
    spells: tuple[int, int], ctx: MatchContext, names: Names
) -> tuple[tuple[int, int], list[str]]:
    if not ctx.enemies_known or not ctx.on_summoners_rift:
        return spells, []
    reasons: list[str] = []
    available = _availability(ctx, names)

    def swap(old: int, new: int, why: str) -> bool:
        nonlocal spells
        if old not in spells or new in spells or not available(new):
            return False
        a, b = (new if s == old else s for s in spells)
        spells = (a, b)
        reasons.append(f"{names.spell_name(new)} en lugar de {names.spell_name(old)}: {why}")
        return True

    opponent = ctx.confident_lane_opponent
    if ctx.position == Position.JUNGLE and SMITE not in spells:
        other = next((s for s in spells if s != FLASH), spells[1])
        swap(other, SMITE, "imprescindible en la jungla")

    elif ctx.position == Position.BOTTOM and ctx.hard_cc_count >= 3:
        why = f"el equipo enemigo tiene {ctx.hard_cc_count} campeones con control fuerte"
        swap(HEAL, CLEANSE, why) or swap(BARRIER, CLEANSE, why)

    elif ctx.position == Position.UTILITY and ctx.dive_threats >= DIVE_THREATS:
        swap(IGNITE, EXHAUST, f"{ctx.dive_threats} asesinos enemigos pueden lanzarse a tu tirador")

    elif (
        ctx.position == Position.MIDDLE
        and opponent is not None
        and opponent.is_dive_threat
        and ctx.me is not None
        and ctx.me.is_mage
        and not ctx.me.is_assassin
        and ctx.me.is_squishy
        and ctx.me.mobility <= 1
    ):
        swap(
            TELEPORT,
            BARRIER,
            f"tu rival ({names.champion_name(opponent.id)}) es un asesino "
            "y tú tienes poca movilidad",
        )
    return spells, reasons


def enforce_smite(
    spells: tuple[int, int], ctx: MatchContext, names: Names
) -> tuple[tuple[int, int], list[str]]:
    """Aplastar en la jungla y sin Aplastar en las demás posiciones (aunque no se vean rivales)."""
    rule = smite_rule(ctx.position, ctx.map_id)
    available = _availability(ctx, names)
    if rule == SmiteRule.REQUIRED and SMITE not in spells and available(SMITE):
        keep = FLASH if FLASH in spells else spells[0]
        result = (keep, SMITE)
        old = spells[1] if spells[0] == keep else spells[0]
        return result, [f"{names.spell_name(SMITE)} en lugar de {names.spell_name(old)}: "
                        "imprescindible en la jungla"]  # fmt: skip
    if rule == SmiteRule.FORBIDDEN and SMITE in spells:
        other = spells[1] if spells[0] == SMITE else spells[0]
        options = (FLASH, *INSTEAD_OF_SMITE.get(ctx.position, ()), IGNITE, TELEPORT, HEAL)
        new = next((s for s in options if s != other and available(s)), None)
        if new is None:
            return spells, []
        result = (new, other) if spells[0] == SMITE else (other, new)
        return result, [f"{names.spell_name(new)} en lugar de {names.spell_name(SMITE)}: "
                        "Aplastar solo sirve en la jungla"]  # fmt: skip
    return spells, []


def keep_flash_key(spells: tuple[int, int], current: tuple[int, int]) -> tuple[int, int]:
    """Deja Destello en la tecla (D o F) en la que ya lo tienes."""
    if FLASH not in spells or FLASH not in current:
        return spells
    other = spells[0] if spells[1] == FLASH else spells[1]
    return (FLASH, other) if current.index(FLASH) == 0 else (other, FLASH)


def _availability(ctx: MatchContext, names: Names) -> Callable[[int], bool]:
    def available(spell_id: int) -> bool:
        spell = names.spell(spell_id)
        return spell is not None and spell.available_in(ctx.game_mode)

    return available
