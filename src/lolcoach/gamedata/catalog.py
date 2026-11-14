"""Catálogo de datos del juego leído del propio cliente.

El cliente ya trae los datos de su parche y en tu idioma
(``/lol-game-data/assets/...``), así que no dependemos de webs externas: siempre
coinciden con tu versión y se cargan en milisegundos.

* Datos generales (campeones, runas, hechizos, posiciones): al conectar, una
  vez por parche.
* Perfiles de campeón y runas recomendadas: bajo demanda (solo los campeones de
  tu partida) y en caché hasta que cambie el parche.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..lcu import endpoints
from ..lcu.client import LcuClient, LcuError
from ..models import Position
from ..util import as_dict, as_int, as_list, as_str
from .items import Item
from .runes import PerkStyle, RecommendedPage

log = logging.getLogger(__name__)

LOAD_ATTEMPTS = 3  # recién abierto, el cliente puede tardar en servir alguna lista
LOAD_RETRY_DELAY = 2.0
GAME_ASSETS = "/lol-game-data/assets/"  # solo iconos de los recursos del juego
# Lo que se carga al conectar: (ruta, nombre para los avisos)
STATIC_DATA = (
    (endpoints.CHAMPION_SUMMARY, "campeones"),
    (endpoints.PERKS, "runas"),
    (endpoints.PERK_STYLES, "árboles"),
    (endpoints.SUMMONER_SPELLS, "hechizos"),
    (endpoints.RECOMMENDED_POSITIONS, "posiciones"),
    (endpoints.ITEMS, "objetos"),
)


@dataclass(frozen=True, slots=True)
class Champion:
    id: int
    name: str  # localizado ("Kai'Sa", "Nunu y Willump"...)
    alias: str  # clave interna estable ("Kaisa", "Nunu"...)
    roles: tuple[str, ...]  # "mage", "assassin", "tank"...


class DamageType(StrEnum):
    MAGIC = "kMagic"
    PHYSICAL = "kPhysical"
    MIXED = "kMixed"
    UNKNOWN = ""

    @classmethod
    def parse(cls, raw: object) -> DamageType:
        try:
            return cls(raw) if isinstance(raw, str) else cls.UNKNOWN
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class ChampionProfile:
    """Cómo juega un campeón, según los datos oficiales del cliente (valores 0-3)."""

    id: int
    roles: tuple[str, ...]
    damage_type: DamageType
    ranged: bool
    damage: int
    durability: int
    crowd_control: int
    mobility: int

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ChampionProfile:
        tactical = as_dict(data.get("tacticalInfo"))
        playstyle = as_dict(data.get("playstyleInfo"))
        return cls(
            id=as_int(data.get("id")),
            roles=tuple(r for r in as_list(data.get("roles")) if isinstance(r, str)),
            damage_type=DamageType.parse(tactical.get("damageType")),
            ranged=tactical.get("attackType") == "ranged",
            damage=as_int(playstyle.get("damage")),
            durability=as_int(playstyle.get("durability")),
            crowd_control=as_int(playstyle.get("crowdControl")),
            mobility=as_int(playstyle.get("mobility")),
        )

    @property
    def is_assassin(self) -> bool:
        return "assassin" in self.roles

    @property
    def is_mage(self) -> bool:
        return "mage" in self.roles

    @property
    def is_burst_threat(self) -> bool:
        """Mucho daño explosivo: asesinos con daño máximo."""
        return self.is_assassin and self.damage >= 3

    @property
    def is_dive_threat(self) -> bool:
        """Asesinos muy móviles que se lanzan sobre objetivos frágiles."""
        return self.is_burst_threat and self.mobility >= 3

    @property
    def is_ranged_poke(self) -> bool:
        """Castiga desde lejos durante la fase de líneas."""
        return self.ranged and (self.is_mage or "marksman" in self.roles) and self.damage >= 2

    @property
    def is_squishy(self) -> bool:
        return self.durability <= 1


@dataclass(frozen=True, slots=True)
class SummonerSpell:
    id: int
    name: str
    game_modes: frozenset[str]

    def available_in(self, game_mode: str) -> bool:
        return not game_mode or not self.game_modes or game_mode in self.game_modes


class GameData:
    def __init__(self) -> None:
        self._version: str | None = None
        self._champions: dict[int, Champion] = {}
        self._perks: dict[int, str] = {}
        self._styles: dict[int, PerkStyle] = {}
        self._spells: dict[int, SummonerSpell] = {}
        self._icons: dict[tuple[str, int], str] = {}  # ("perk"|"style"|"spell", id) -> ruta
        self._positions: dict[int, tuple[Position, ...]] = {}
        self._items: dict[int, Item] = {}
        self._alias_index: dict[str, int] = {}
        self._profiles: dict[int, ChampionProfile] = {}
        self._recommended: dict[tuple[int, Position, int], tuple[RecommendedPage, ...]] = {}

    # --- Carga --------------------------------------------------------------

    @property
    def version(self) -> str | None:
        return self._version

    @property
    def champion_count(self) -> int:
        return len(self._champions)

    async def load(
        self, client: LcuClient, version: str, *, retry_delay: float = LOAD_RETRY_DELAY
    ) -> None:
        """Carga los datos del parche. Lo que falle se reintenta unas veces; si aun así falta
        algo, el parche no se da por cargado y se vuelve a intentar en la próxima conexión
        (antes, una lista de objetos que fallaba al abrir el cliente dejaba la tienda vacía
        toda la sesión)."""
        if version == self._version:
            return
        loaded: dict[str, Any] = {}
        for attempt in range(LOAD_ATTEMPTS):
            missing = [path for path, _ in STATIC_DATA if path not in loaded]
            results = await asyncio.gather(
                *(client.get(path) for path in missing), return_exceptions=True
            )
            failed: list[tuple[str, BaseException]] = []
            for path, result in zip(missing, results, strict=True):
                if isinstance(result, BaseException):
                    failed.append((path, result))
                else:
                    loaded[path] = result
            if not failed:
                break
            if attempt == LOAD_ATTEMPTS - 1:
                names = dict(STATIC_DATA)
                for path, exc in failed:
                    log.warning("No se pudieron cargar los datos de %s: %s", names[path], exc)
            else:
                await asyncio.sleep(retry_delay)
        self._apply_static(loaded)
        self._profiles.clear()
        self._recommended.clear()
        complete = len(loaded) == len(STATIC_DATA) and bool(self._champions)
        self._version = version if complete else None
        log.debug(
            "Datos del parche %s: %d campeones, %d runas, %d árboles, %d hechizos, %d objetos",
            version, len(self._champions), len(self._perks), len(self._styles), len(self._spells),
            len(self._items),
        )  # fmt: skip

    def _apply_static(self, loaded: Mapping[str, Any]) -> None:
        """Cambia solo lo que ha llegado: lo que falló conserva lo que hubiera."""
        if (summary := loaded.get(endpoints.CHAMPION_SUMMARY)) is not None:
            self._champions = _parse_champions(summary)
            self._alias_index = {}
        if (perks := loaded.get(endpoints.PERKS)) is not None:
            self._perks = {
                as_int(p.get("id")): as_str(p.get("name"))
                for p in as_list(perks)
                if isinstance(p, dict)
            }
            self._set_icons("perk", perks)
        if (styles := loaded.get(endpoints.PERK_STYLES)) is not None:
            self._styles = {
                style.id: style
                for style in (
                    PerkStyle.from_json(s)
                    for s in as_list(as_dict(styles).get("styles"))
                    if isinstance(s, dict)
                )
            }
            self._set_icons("style", as_dict(styles).get("styles"))
        if (spells := loaded.get(endpoints.SUMMONER_SPELLS)) is not None:
            self._spells = _parse_spells(spells)
            self._set_icons("spell", spells)
        if (positions := loaded.get(endpoints.RECOMMENDED_POSITIONS)) is not None:
            self._positions = _parse_positions(positions)
        if (items := loaded.get(endpoints.ITEMS)) is not None:
            self._items = _parse_items(items)

    def load_static(self, data: Mapping[str, Any], version: str) -> None:
        """Carga desde un diccionario ya leído (pruebas y herramientas)."""
        self._champions = _parse_champions(data.get("championSummary"))
        self._alias_index = {}
        self._perks = {as_int(p["id"]): as_str(p["name"]) for p in as_list(data.get("perks"))}
        self._styles = {
            s.id: s
            for s in (PerkStyle.from_json(x) for x in as_dict(data.get("perkstyles"))["styles"])
        }
        self._spells = _parse_spells(data.get("summonerSpells"))
        self._icons = {}
        self._set_icons("perk", data.get("perks"))
        self._set_icons("style", as_dict(data.get("perkstyles")).get("styles"))
        self._set_icons("spell", data.get("summonerSpells"))
        self._positions = _parse_positions(data.get("recommendedPositions"))
        self._items = _parse_items(data.get("items"))
        self._profiles = {
            as_int(k): ChampionProfile.from_json(v)
            for k, v in as_dict(data.get("champions")).items()
        }
        self._version = version

    def _set_icons(self, kind: str, entries: Any) -> None:
        """Rutas de los iconos; solo se aceptan recursos del juego."""
        self._icons = {key: path for key, path in self._icons.items() if key[0] != kind}
        for entry in as_list(entries):
            if not isinstance(entry, dict):
                continue
            ident, path = as_int(entry.get("id")), as_str(entry.get("iconPath"))
            if ident > 0 and path.startswith(GAME_ASSETS):
                self._icons[(kind, ident)] = path

    # --- Consultas inmediatas ---------------------------------------------------

    def icon_path(self, kind: str, ident: int) -> str | None:
        """Ruta en el cliente del icono de una runa (``perk``), árbol (``style``) o hechizo."""
        return self._icons.get((kind, ident))

    def champion(self, champion_id: int) -> Champion | None:
        return self._champions.get(champion_id)

    def champion_name(self, champion_id: int) -> str:
        if champion_id <= 0:
            return "?"
        champion = self._champions.get(champion_id)
        return champion.name if champion else f"Campeón #{champion_id}"

    def perk_name(self, perk_id: int) -> str:
        return self._perks.get(perk_id, f"runa #{perk_id}")

    @property
    def styles(self) -> Mapping[int, PerkStyle]:
        return self._styles

    def style_name(self, style_id: int) -> str:
        style = self._styles.get(style_id)
        return style.name if style else f"árbol #{style_id}"

    def spell(self, spell_id: int) -> SummonerSpell | None:
        return self._spells.get(spell_id)

    def spell_name(self, spell_id: int) -> str:
        spell = self._spells.get(spell_id)
        return spell.name if spell else f"hechizo #{spell_id}"

    def positions_for(self, champion_id: int) -> tuple[Position, ...]:
        """Posiciones habituales del campeón, de más a menos frecuente."""
        return self._positions.get(champion_id, ())

    def item(self, item_id: int) -> Item | None:
        return self._items.get(item_id)

    def item_name(self, item_id: int) -> str:
        item = self._items.get(item_id)
        return item.name if item else f"objeto #{item_id}"

    def is_buyable(self, item_id: int) -> bool:
        """El objeto existe en este parche y se puede comprar en la Grieta."""
        item = self._items.get(item_id)
        return item is not None and item.buyable

    def champion_id_by_alias(self, alias: str) -> int:
        """Id a partir de la clave interna ("Kaisa", "DrMundo"...) o del nombre visible
        ("Kai'Sa", "Dr. Mundo"); 0 si no existe."""
        if not self._alias_index and self._champions:
            index: dict[str, int] = {}
            for champion in self._champions.values():
                index.setdefault(_normalized(champion.name), champion.id)
            for champion in self._champions.values():  # la clave interna manda
                index[_normalized(champion.alias)] = champion.id
            self._alias_index = index
        return self._alias_index.get(_normalized(alias), 0)

    # --- Consultas al cliente (con caché) ------------------------------------------

    async def profile(self, client: LcuClient, champion_id: int) -> ChampionProfile | None:
        if champion_id <= 0:
            return None
        cached = self._profiles.get(champion_id)
        if cached is not None:
            return cached
        try:
            data = await client.get(endpoints.CHAMPION_DETAILS.format(champion_id=champion_id))
        except LcuError as exc:
            log.debug("Sin perfil para el campeón %s: %s", champion_id, exc)
            return None
        if not isinstance(data, dict):
            return None
        profile = ChampionProfile.from_json(data)
        self._profiles[champion_id] = profile
        return profile

    async def profiles(
        self, client: LcuClient, champion_ids: Iterable[int]
    ) -> dict[int, ChampionProfile]:
        ids = list(dict.fromkeys(c for c in champion_ids if c > 0))
        found = await asyncio.gather(*(self.profile(client, c) for c in ids))
        return {c: p for c, p in zip(ids, found, strict=True) if p is not None}

    async def recommended_pages(
        self, client: LcuClient, champion_id: int, position: Position, map_id: int
    ) -> tuple[RecommendedPage, ...]:
        key = (champion_id, position, map_id)
        cached = self._recommended.get(key)
        if cached is not None:
            return cached
        path = endpoints.RECOMMENDED_PAGES.format(
            champion_id=champion_id, position=position.api_name, map_id=map_id
        )
        try:
            data = await client.get(path)
        except LcuError as exc:
            log.debug("Sin runas recomendadas (%s): %s", path, exc)
            return ()
        pages = tuple(p for p in map(RecommendedPage.from_json, as_list(data)) if p is not None)
        self._recommended[key] = pages
        return pages


def _parse_champions(raw: Any) -> dict[int, Champion]:
    champions: dict[int, Champion] = {}
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        champion_id = as_int(item.get("id"))
        if champion_id <= 0:  # -1 = "Ninguno"
            continue
        champions[champion_id] = Champion(
            id=champion_id,
            name=as_str(item.get("name")) or as_str(item.get("alias")) or f"#{champion_id}",
            alias=as_str(item.get("alias")),
            roles=tuple(r for r in as_list(item.get("roles")) if isinstance(r, str)),
        )
    return champions


def _parse_spells(raw: Any) -> dict[int, SummonerSpell]:
    spells: dict[int, SummonerSpell] = {}
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        spell_id = as_int(item.get("id"))
        if spell_id <= 0 or spell_id in spells:
            continue
        spells[spell_id] = SummonerSpell(
            id=spell_id,
            name=as_str(item.get("name")),
            game_modes=frozenset(m for m in as_list(item.get("gameModes")) if isinstance(m, str)),
        )
    return spells


def _parse_items(raw: Any) -> dict[int, Item]:
    items: dict[int, Item] = {}
    for entry in as_list(raw):
        if isinstance(entry, dict):
            item = Item.from_json(entry)
            if item.id > 0:
                items[item.id] = item
    return items


def _parse_positions(raw: Any) -> dict[int, tuple[Position, ...]]:
    positions: dict[int, tuple[Position, ...]] = {}
    for key, value in as_dict(raw).items():
        parsed = tuple(
            p
            for p in (
                Position.parse(x) for x in as_list(as_dict(value).get("recommendedPositions"))
            )
            if p != Position.NONE
        )
        if parsed:
            positions[as_int(key)] = parsed
    return positions


def _normalized(name: str) -> str:
    """ "Kai'Sa" -> "kaisa", "Dr. Mundo" -> "drmundo": para comparar claves y nombres."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())
