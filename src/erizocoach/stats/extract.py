"""De una partida (y su línea temporal) a las builds de sus 10 jugadores.

Privacidad: solo se guarda lo necesario para estadísticas de builds (campeón,
posición, resultado, compras en orden, runas y hechizos). Ni nombres, ni Riot ID,
ni PUUID.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..util import as_dict, as_int, as_list, as_str

MIN_GAME_SECONDS = 15 * 60  # partidas más cortas (rendiciones tempranas, remakes) no sirven
POSITIONS = frozenset({"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"})
RUNE_FIELDS = 11  # árbol principal, árbol secundario y las 9 runas de la página
SHARD_KEYS = ("offense", "flex", "defense")  # filas de fragmentos, en orden


@dataclass(frozen=True, slots=True)
class PlayerBuild:
    champion_id: int
    position: str
    win: bool
    purchases: tuple[tuple[int, int], ...]  # (segundo de partida, objeto), en orden
    final_items: tuple[int, ...] = ()
    runes: tuple[int, ...] = ()  # (árbol principal, secundario, 9 runas); () = sin datos
    spells: tuple[int, ...] = ()  # (hechizo D, hechizo F); () = sin datos

    @property
    def has_loadout(self) -> bool:
        return len(self.runes) == RUNE_FIELDS and len(self.spells) == 2

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "c": self.champion_id,
            "p": self.position,
            "w": int(self.win),
            "b": [list(p) for p in self.purchases],
            "f": list(self.final_items),
        }
        if self.has_loadout:
            data["r"] = list(self.runes)
            data["s"] = list(self.spells)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PlayerBuild:
        return cls(
            champion_id=as_int(data.get("c")),
            position=as_str(data.get("p")),
            win=bool(data.get("w")),
            purchases=tuple((as_int(t), as_int(i)) for t, i in as_list(data.get("b"))),
            final_items=tuple(as_int(i) for i in as_list(data.get("f"))),
            runes=tuple(as_int(r) for r in as_list(data.get("r"))),
            spells=tuple(as_int(s) for s in as_list(data.get("s"))),
        )


def patch_of(version: str) -> str:
    """``"16.22.834.8910"`` -> ``"16.22"``."""
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else ""


def patch_key(patch: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in patch.split("."))
    except ValueError:
        return ()


def match_patch(match: Any) -> str:
    return patch_of(as_str(as_dict(as_dict(match).get("info")).get("gameVersion")))


def extract_builds(match: Any, timeline: Any) -> list[PlayerBuild]:
    info = as_dict(as_dict(match).get("info"))
    if as_int(info.get("gameDuration")) < MIN_GAME_SECONDS:
        return []
    purchases = _purchases_by_participant(timeline)
    builds: list[PlayerBuild] = []
    for raw in as_list(info.get("participants")):
        p = as_dict(raw)
        position = as_str(p.get("teamPosition"))
        if position not in POSITIONS or p.get("gameEndedInEarlySurrender"):
            continue
        participant_id = as_int(p.get("participantId"))
        runes, spells = extract_loadout(p)
        builds.append(
            PlayerBuild(
                champion_id=as_int(p.get("championId")),
                position=position,
                win=bool(p.get("win")),
                purchases=tuple(purchases.get(participant_id, ())),
                final_items=tuple(
                    i for i in (as_int(p.get(f"item{n}")) for n in range(6)) if i > 0
                ),
                runes=runes,
                spells=spells,
            )
        )
    return builds


def extract_loadout(participant: Any) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Runas (árboles + 9 runas, en el orden de las páginas del cliente) y hechizos."""
    p = as_dict(participant)
    perks = as_dict(p.get("perks"))
    styles: dict[str, Mapping[str, Any]] = {
        as_str(as_dict(s).get("description")): as_dict(s) for s in as_list(perks.get("styles"))
    }
    primary, sub = styles.get("primaryStyle", {}), styles.get("subStyle", {})

    def chosen(style: Mapping[str, Any]) -> list[int]:
        return [as_int(as_dict(sel).get("perk")) for sel in as_list(style.get("selections"))]

    main, secondary = chosen(primary), chosen(sub)
    shards = [as_int(as_dict(perks.get("statPerks")).get(key)) for key in SHARD_KEYS]
    runes = (as_int(primary.get("style")), as_int(sub.get("style")), *main, *secondary, *shards)
    if len(main) != 4 or len(secondary) != 2 or any(r <= 0 for r in runes):
        runes = ()
    spells = (as_int(p.get("summoner1Id")), as_int(p.get("summoner2Id")))
    if any(s <= 0 for s in spells):
        spells = ()
    return runes, spells


def attach_loadouts(builds: list[PlayerBuild], match: Any) -> list[PlayerBuild]:
    """Completa con runas y hechizos builds guardadas antes de que se recogieran."""
    loadouts = {
        as_int(as_dict(p).get("championId")): extract_loadout(p)
        for p in as_list(as_dict(as_dict(match).get("info")).get("participants"))
    }
    result: list[PlayerBuild] = []
    for build in builds:
        runes, spells = loadouts.get(build.champion_id, ((), ()))
        result.append(dataclasses.replace(build, runes=runes, spells=spells))
    return result


def _purchases_by_participant(timeline: Any) -> dict[int, list[tuple[int, int]]]:
    """Compras en orden; las compras deshechas (ITEM_UNDO) se eliminan."""
    result: dict[int, list[tuple[int, int]]] = defaultdict(list)
    frames = as_list(as_dict(as_dict(timeline).get("info")).get("frames"))
    events = sorted(
        (as_dict(e) for frame in frames for e in as_list(as_dict(frame).get("events"))),
        key=lambda e: as_int(e.get("timestamp")),
    )
    for event in events:
        kind = event.get("type")
        participant = as_int(event.get("participantId"))
        if kind == "ITEM_PURCHASED":
            item = as_int(event.get("itemId"))
            if item > 0:
                result[participant].append((as_int(event.get("timestamp")) // 1000, item))
        elif kind == "ITEM_UNDO":
            undone = as_int(event.get("beforeId"))
            if undone > 0 and as_int(event.get("afterId")) == 0:
                bought = result[participant]
                for index in range(len(bought) - 1, -1, -1):
                    if bought[index][1] == undone:
                        del bought[index]
                        break
    return result
