"""De una partida (y su línea temporal) a las builds de sus 10 jugadores.

Privacidad: solo se guarda lo necesario para estadísticas de builds (campeón,
posición, resultado y compras en orden). Ni nombres, ni Riot ID, ni PUUID.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..util import as_dict, as_int, as_list, as_str

MIN_GAME_SECONDS = 15 * 60  # partidas más cortas (rendiciones tempranas, remakes) no sirven
POSITIONS = frozenset({"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"})


@dataclass(frozen=True, slots=True)
class PlayerBuild:
    champion_id: int
    position: str
    win: bool
    purchases: tuple[tuple[int, int], ...]  # (segundo de partida, objeto), en orden
    final_items: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "c": self.champion_id,
            "p": self.position,
            "w": int(self.win),
            "b": [list(p) for p in self.purchases],
            "f": list(self.final_items),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PlayerBuild:
        return cls(
            champion_id=as_int(data.get("c")),
            position=as_str(data.get("p")),
            win=bool(data.get("w")),
            purchases=tuple((as_int(t), as_int(i)) for t, i in as_list(data.get("b"))),
            final_items=tuple(as_int(i) for i in as_list(data.get("f"))),
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
        builds.append(
            PlayerBuild(
                champion_id=as_int(p.get("championId")),
                position=position,
                win=bool(p.get("win")),
                purchases=tuple(purchases.get(participant_id, ())),
                final_items=tuple(
                    i for i in (as_int(p.get(f"item{n}")) for n in range(6)) if i > 0
                ),
            )
        )
    return builds


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
