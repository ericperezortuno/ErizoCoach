"""De miles de partidas a la build más habitual de cada campeón y posición.

Para cada (campeón, posición):

* **Inicio**: el conjunto de objetos comprado en el primer minuto más repetido.
* **Botas**: las botas (nivel 2) que más se compran, y en qué % de partidas.
* **Núcleo**: los 3 primeros objetos legendarios, elegidos en cadena para respetar
  el orden real: el 1.º más habitual; el 2.º más habitual entre quienes hicieron ese
  1.º; y el 3.º igual. Si hay pocos datos para la cadena, se usa el más habitual en
  esa posición.
* **Siguientes**: los legendarios más comprados después del núcleo.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..builds.models import ItemCount
from ..gamedata.items import Item
from ..util import as_dict, as_int, as_list, as_str
from .extract import PlayerBuild

STARTING_SECONDS = 60
TRINKETS = frozenset({3340, 3363, 3364})  # guardianes/lentes: no forman parte de la build
LEGENDARY_MIN_PRICE = 2200
CHAIN_MIN_SUPPORT = 5  # partidas mínimas para fiarse de la cadena del núcleo
FORMAT_VERSION = 1

ItemLookup = Callable[[int], Item | None]


@dataclass(frozen=True, slots=True)
class ChampionStats:
    champion_id: int
    position: str
    games: int
    wins: int
    starting: tuple[ItemCount, ...]
    boots: int | None
    boots_rate: float
    core: tuple[int, ...]
    extra: tuple[int, ...]

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def is_legendary(item: Item | None) -> bool:
    return (
        item is not None
        and item.buyable
        and not item.is_boots
        and item.price_total >= LEGENDARY_MIN_PRICE
        and not any(i < 10_000 for i in item.into_ids)  # objeto final en la Grieta
    )


def is_upgraded_boots(item: Item | None) -> bool:
    return item is not None and item.is_boots and 1001 in item.from_ids


def aggregate(
    builds: Iterable[PlayerBuild], item: ItemLookup
) -> dict[tuple[int, str], ChampionStats]:
    groups: dict[tuple[int, str], list[PlayerBuild]] = {}
    for build in builds:
        groups.setdefault((build.champion_id, build.position), []).append(build)
    return {key: _summarize(key, games, item) for key, games in groups.items()}


def _summarize(key: tuple[int, str], games: list[PlayerBuild], item: ItemLookup) -> ChampionStats:
    starts: Counter[tuple[int, ...]] = Counter()
    boots: Counter[int] = Counter()
    sequences: list[list[int]] = []
    for game in games:
        start = tuple(
            sorted(i for t, i in game.purchases if t <= STARTING_SECONDS and i not in TRINKETS)
        )
        if start:
            starts[start] += 1
        first_boots = next((i for _, i in game.purchases if is_upgraded_boots(item(i))), None)
        if first_boots is not None:
            boots[first_boots] += 1
        sequence: list[int] = []
        for _, item_id in game.purchases:
            if item_id not in sequence and is_legendary(item(item_id)):
                sequence.append(item_id)
        sequences.append(sequence)

    core = _core_chain(sequences)
    later: Counter[int] = Counter(i for s in sequences for i in s[3:6] if i not in core)
    overall: Counter[int] = Counter(i for s in sequences for i in s if i not in core)
    extra = [i for i, _ in later.most_common(3)]
    for item_id, _ in overall.most_common():
        if len(extra) >= 3:
            break
        if item_id not in extra:
            extra.append(item_id)

    start_items = starts.most_common(1)[0][0] if starts else ()
    counted = Counter(start_items)
    boots_item = boots.most_common(1)[0][0] if boots else None
    return ChampionStats(
        champion_id=key[0],
        position=key[1],
        games=len(games),
        wins=sum(g.win for g in games),
        starting=tuple(
            ItemCount(i, n)
            for i, n in sorted(counted.items(), key=lambda x: -item_price(item, x[0]))
        ),
        boots=boots_item,
        boots_rate=round(sum(boots.values()) / len(games), 3) if games else 0.0,
        core=tuple(core),
        extra=tuple(extra),
    )


def item_price(item: ItemLookup, item_id: int) -> int:
    found = item(item_id)
    return found.price_total if found else 0


def _core_chain(sequences: list[list[int]]) -> list[int]:
    core: list[int] = []
    for slot in range(3):
        matching = [s for s in sequences if s[:slot] == core and len(s) > slot]
        pool = (
            matching
            if len(matching) >= CHAIN_MIN_SUPPORT
            else [s for s in sequences if len(s) > slot]
        )
        counts = Counter(s[slot] for s in pool if s[slot] not in core)
        if not counts:
            counts = Counter(i for s in sequences for i in s if i not in core)
        if not counts:
            break
        core.append(counts.most_common(1)[0][0])
    return core


# --- Guardado ----------------------------------------------------------------------------


def save_stats(
    path: Path, patch: str, matches: int, stats: dict[tuple[int, str], ChampionStats]
) -> None:
    payload = {
        "format": FORMAT_VERSION,
        "patch": patch,
        "matches": matches,
        "generated": int(time.time()),
        "builds": {
            f"{champion}:{position}": {
                **asdict(entry),
                "starting": [[s.item_id, s.count] for s in entry.starting],
            }
            for (champion, position), entry in stats.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)  # escritura atómica: nunca queda un archivo a medias


def load_stats(path: Path) -> tuple[str, int, dict[tuple[int, str], ChampionStats]] | None:
    try:
        payload = as_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    if payload.get("format") != FORMAT_VERSION:
        return None
    stats: dict[tuple[int, str], ChampionStats] = {}
    for raw in as_dict(payload.get("builds")).values():
        entry = _entry(as_dict(raw))
        stats[(entry.champion_id, entry.position)] = entry
    return as_str(payload.get("patch")), as_int(payload.get("matches")), stats


def _entry(raw: dict[str, Any]) -> ChampionStats:
    boots = raw.get("boots")
    return ChampionStats(
        champion_id=as_int(raw.get("champion_id")),
        position=as_str(raw.get("position")),
        games=as_int(raw.get("games")),
        wins=as_int(raw.get("wins")),
        starting=tuple(ItemCount(as_int(i), as_int(n, 1)) for i, n in as_list(raw.get("starting"))),
        boots=as_int(boots) if boots is not None else None,
        boots_rate=float(raw.get("boots_rate") or 0.0),
        core=tuple(as_int(i) for i in as_list(raw.get("core"))),
        extra=tuple(as_int(i) for i in as_list(raw.get("extra"))),
    )
