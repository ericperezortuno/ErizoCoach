"""De miles de partidas a la build más habitual de cada campeón y posición.

Para cada (campeón, posición):

* **Inicio**: el conjunto de objetos comprado en el primer minuto más repetido.
* **Botas**: las botas (nivel 2) que más se compran, y en qué % de partidas.
* **Núcleo**: los 3 primeros objetos legendarios, elegidos en cadena para respetar
  el orden real: el 1.º más habitual; el 2.º más habitual entre quienes hicieron ese
  1.º; y el 3.º igual. Si hay pocos datos para la cadena, se usa el más habitual en
  esa posición.
* **Siguientes**: los legendarios más comprados después del núcleo.
* **Runas**: la piedra angular (con su árbol) más elegida; entre quienes la llevan,
  la runa más elegida de cada fila, el árbol secundario y la pareja de runas
  secundarias más repetidos, y el fragmento más elegido de cada fila. Así la página
  sale siempre de combinaciones que juega la gente.
* **Hechizos**: la pareja más repetida (en el orden de teclas más habitual).
* **Enfrentamientos**: contra cada rival de su misma línea (misma posición en el otro
  equipo), cuántas partidas y cuántas victorias. De ahí salen los counters.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..builds.models import ItemCount
from ..gamedata.items import Item
from ..util import as_dict, as_int, as_list, as_str
from .extract import POSITIONS, RUNE_FIELDS, PlayerBuild

STARTING_SECONDS = 60
TRINKETS = frozenset({3340, 3363, 3364})  # guardianes/lentes: no forman parte de la build
LEGENDARY_MIN_PRICE = 2200
CHAIN_MIN_SUPPORT = 5  # partidas mínimas para fiarse de la cadena del núcleo
FORMAT_VERSION = 2  # 2: con runas y hechizos
# Las opciones de runas (1) y los enfrentamientos (2) son campos nuevos dentro del formato 2:
# las versiones anteriores de la app los ignoran (el paquete compartido les sigue valiendo).
# Esta marca solo sirve para recalcular una caché local hecha antes de que existieran.
RUNE_OPTIONS_VERSION = 2
RUNE_OPTIONS = 3  # piedras angulares distintas que se ofrecen
MIN_OPTION_GAMES = 5  # menos partidas que esto con una piedra angular es ruido
MIN_MATCHUP_GAMES = 2  # enfrentamientos con una sola partida no se guardan (solo ocupan)

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
    loadout_games: int = 0  # partidas con runas y hechizos (las antiguas no los tienen)
    runes: tuple[int, ...] = ()  # (árbol principal, secundario, 9 runas); () = sin datos
    spells: tuple[int, ...] = ()  # (hechizo D, hechizo F)
    # Hasta 3 páginas con piedras angulares distintas, de más a menos jugada:
    # (partidas, victorias, árbol principal, secundario, 9 runas)
    rune_options: tuple[tuple[int, ...], ...] = ()
    # Contra cada rival de línea: (campeón rival, partidas, victorias), de más a menos partidas
    matchups: tuple[tuple[int, int, int], ...] = ()


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


@dataclass(frozen=True, slots=True)
class _Game:
    """De cada build, solo lo que usa el resumen (sin la lista de compras).

    Con 20 000 partidas hay 200 000 builds: guardarlas enteras llevaba la app a ~800 MB
    de memoria que luego Python no devolvía al sistema. Así son unos pocos MB.
    """

    win: bool
    start: tuple[int, ...]  # objetos iniciales, ordenados; () = ninguno
    boots: int | None  # primeras botas de nivel 2
    sequence: tuple[int, ...]  # legendarios en orden de compra, sin repetir
    runes: tuple[int, ...]
    spells: tuple[int, ...]

    @property
    def has_loadout(self) -> bool:
        return len(self.runes) == RUNE_FIELDS and len(self.spells) == 2


class _Compactor:
    """Reduce cada build a un ``_Game``. Las tuplas iguales se comparten (casi todas se
    repiten: mismas runas, mismos hechizos, mismo inicio) y cada objeto se consulta una vez."""

    def __init__(self, item: ItemLookup) -> None:
        self._item = item
        self._shared: dict[tuple[int, ...], tuple[int, ...]] = {}
        self._legendary: dict[int, bool] = {}
        self._boots: dict[int, bool] = {}

    def __call__(self, build: PlayerBuild) -> _Game:
        purchases = build.purchases
        start = tuple(
            sorted(i for t, i in purchases if t <= STARTING_SECONDS and i not in TRINKETS)
        )
        first_boots = next((i for _, i in purchases if self._is_boots(i)), None)
        sequence: list[int] = []
        for _, item_id in purchases:
            if item_id not in sequence and self._is_legendary(item_id):
                sequence.append(item_id)
        return _Game(
            win=build.win,
            start=self._share(start),
            boots=first_boots,
            sequence=self._share(tuple(sequence)),
            runes=self._share(build.runes),
            spells=self._share(build.spells),
        )

    def _share(self, value: tuple[int, ...]) -> tuple[int, ...]:
        return self._shared.setdefault(value, value)

    def _is_legendary(self, item_id: int) -> bool:
        found = self._legendary.get(item_id)
        if found is None:
            found = self._legendary[item_id] = is_legendary(self._item(item_id))
        return found

    def _is_boots(self, item_id: int) -> bool:
        found = self._boots.get(item_id)
        if found is None:
            found = self._boots[item_id] = is_upgraded_boots(self._item(item_id))
        return found


def aggregate(
    builds: Iterable[PlayerBuild], item: ItemLookup
) -> dict[tuple[int, str], ChampionStats]:
    compact = _Compactor(item)
    groups: dict[tuple[int, str], list[_Game]] = {}
    for build in builds:
        groups.setdefault((build.champion_id, build.position), []).append(compact(build))
    return {key: _summarize(key, games, item) for key, games in groups.items()}


def _summarize(key: tuple[int, str], games: list[_Game], item: ItemLookup) -> ChampionStats:
    starts: Counter[tuple[int, ...]] = Counter(g.start for g in games if g.start)
    boots: Counter[int] = Counter(g.boots for g in games if g.boots is not None)
    sequences = [g.sequence for g in games]

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
    with_loadout = [g for g in games if g.has_loadout]
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
        loadout_games=len(with_loadout),
        runes=_rune_page(with_loadout),
        spells=_spell_pair(with_loadout),
        rune_options=_rune_options(with_loadout),
    )


MatchupCounts = dict[tuple[int, str], dict[int, list[int]]]


def lane_matchups(matches: Iterable[list[PlayerBuild]]) -> MatchupCounts:
    """Partidas y victorias de cada (campeón, posición) contra cada rival de su línea.

    En cada partida, el rival de línea es el del otro equipo con la misma posición: el
    único con esa posición que ha tenido el resultado contrario. Si una posición no está
    clara (dos jugadores en la misma, o falta), esa línea no cuenta.
    """
    counts: MatchupCounts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for builds in matches:
        seats: dict[str, list[PlayerBuild]] = defaultdict(list)
        for build in builds:
            seats[build.position].append(build)
        for position, pair in seats.items():
            if position not in POSITIONS or len(pair) != 2 or pair[0].win == pair[1].win:
                continue
            for me, rival in (pair, pair[::-1]):
                row = counts[(me.champion_id, position)][rival.champion_id]
                row[0] += 1
                row[1] += int(me.win)
    return counts


def with_matchups(
    stats: dict[tuple[int, str], ChampionStats], counts: MatchupCounts
) -> dict[tuple[int, str], ChampionStats]:
    """Añade a cada entrada sus enfrentamientos de línea (los de 2 partidas o más)."""
    result = dict(stats)
    for key, entry in stats.items():
        rows = counts.get(key, {})
        matchups = sorted(
            ((rival, games, wins) for rival, (games, wins) in rows.items()
             if games >= MIN_MATCHUP_GAMES),
            key=lambda row: (-row[1], row[0]),
        )  # fmt: skip
        result[key] = dataclasses.replace(entry, matchups=tuple(matchups))
    return result


def _most_common(values: Iterable[Any]) -> Any:
    counts = Counter(values)
    return counts.most_common(1)[0][0] if counts else None


def _rune_page(games: list[_Game]) -> tuple[int, ...]:
    """(árbol principal, secundario, 9 runas) partiendo de la piedra angular más elegida."""
    if not games:
        return ()
    # runes = (principal, secundario, angular, fila 1, fila 2, fila 3, sec. 1, sec. 2, 3 fragmentos)
    keystone = _most_common(_keystone(g) for g in games)
    return _page_with_keystone([g for g in games if _keystone(g) == keystone], keystone)


def _keystone(game: _Game) -> tuple[int, int]:
    return game.runes[0], game.runes[2]  # (árbol principal, piedra angular)


def _rune_options(games: list[_Game]) -> tuple[tuple[int, ...], ...]:
    """Las páginas de las piedras angulares más jugadas, con sus partidas y victorias."""
    options: list[tuple[int, ...]] = []
    for keystone, count in Counter(_keystone(g) for g in games).most_common(RUNE_OPTIONS):
        if count < MIN_OPTION_GAMES:
            break
        same = [g for g in games if _keystone(g) == keystone]
        wins = sum(g.win for g in same)
        options.append((count, wins, *_page_with_keystone(same, keystone)))
    return tuple(options)


def _page_with_keystone(same: list[_Game], keystone: tuple[int, int]) -> tuple[int, ...]:
    """La página más habitual entre las partidas que llevan esa piedra angular."""
    rows = [_most_common(g.runes[i] for g in same) for i in (3, 4, 5)]
    sub = _most_common(g.runes[1] for g in same)
    with_sub = [g for g in same if g.runes[1] == sub]
    pair_key = _most_common(tuple(sorted(g.runes[6:8])) for g in with_sub)
    pair = _most_common(g.runes[6:8] for g in with_sub if tuple(sorted(g.runes[6:8])) == pair_key)
    shards = [_most_common(g.runes[i] for g in same) for i in (8, 9, 10)]
    return (keystone[0], sub, keystone[1], *rows, *pair, *shards)


def _spell_pair(games: list[_Game]) -> tuple[int, ...]:
    if not games:
        return ()
    pair = _most_common(tuple(sorted(g.spells)) for g in games)
    return tuple(_most_common(g.spells for g in games if tuple(sorted(g.spells)) == pair))


def item_price(item: ItemLookup, item_id: int) -> int:
    found = item(item_id)
    return found.price_total if found else 0


def _core_chain(sequences: list[tuple[int, ...]]) -> list[int]:
    core: list[int] = []
    for slot in range(3):
        prefix = tuple(core)
        matching = [s for s in sequences if s[:slot] == prefix and len(s) > slot]
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


Stats = tuple[str, int, dict[tuple[int, str], ChampionStats]]  # (parche, partidas, builds)


def stats_payload(
    patch: str, matches: int, stats: dict[tuple[int, str], ChampionStats], loadouts: int = 0
) -> dict[str, Any]:
    """Las estadísticas en JSON (la caché local y el paquete compartido usan el mismo)."""
    return {
        "format": FORMAT_VERSION,
        "patch": patch,
        "matches": matches,
        "loadouts": loadouts,  # partidas con runas y hechizos (para saber si hay que recalcular)
        "rune_options": RUNE_OPTIONS_VERSION,
        "generated": int(time.time()),
        "builds": {
            f"{champion}:{position}": {
                **asdict(entry),
                "starting": [[s.item_id, s.count] for s in entry.starting],
            }
            for (champion, position), entry in sorted(stats.items())
        },
    }


def parse_payload(
    payload: Any, accept: Callable[[ChampionStats], bool] = lambda _entry: True
) -> Stats | None:
    """Lee el JSON de :func:`stats_payload`; ``accept`` descarta entradas que no lo pasen."""
    payload = as_dict(payload)
    if payload.get("format") != FORMAT_VERSION:
        return None
    stats: dict[tuple[int, str], ChampionStats] = {}
    for raw in as_dict(payload.get("builds")).values():
        entry = _entry(as_dict(raw))
        if accept(entry):
            stats[(entry.champion_id, entry.position)] = entry
    return as_str(payload.get("patch")), as_int(payload.get("matches")), stats


def save_stats(
    path: Path,
    patch: str,
    matches: int,
    stats: dict[tuple[int, str], ChampionStats],
    loadouts: int = 0,
) -> None:
    payload = stats_payload(patch, matches, stats, loadouts)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)  # escritura atómica: nunca queda un archivo a medias


def is_fresh(path: Path, matches: int, loadouts: int) -> bool:
    """La caché se calculó con estas mismas partidas (y runas)."""
    try:
        payload = as_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False
    return (
        payload.get("format") == FORMAT_VERSION
        and as_int(payload.get("matches")) == matches
        and as_int(payload.get("loadouts")) == loadouts
        and payload.get("rune_options") == RUNE_OPTIONS_VERSION
    )


def load_stats(path: Path) -> Stats | None:
    try:
        return parse_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _entry(raw: Mapping[str, Any]) -> ChampionStats:
    boots = raw.get("boots")
    rate = raw.get("boots_rate")
    if isinstance(rate, bool) or not isinstance(rate, int | float):
        rate = 0.0
    return ChampionStats(
        champion_id=as_int(raw.get("champion_id")),
        position=as_str(raw.get("position")),
        games=as_int(raw.get("games")),
        wins=as_int(raw.get("wins")),
        starting=tuple(
            ItemCount(as_int(pair[0]), as_int(pair[1], 1))
            for pair in as_list(raw.get("starting"))
            if isinstance(pair, list) and len(pair) == 2
        ),
        boots=as_int(boots) if boots is not None else None,
        boots_rate=float(rate),
        core=tuple(as_int(i) for i in as_list(raw.get("core"))),
        extra=tuple(as_int(i) for i in as_list(raw.get("extra"))),
        loadout_games=as_int(raw.get("loadout_games")),
        runes=tuple(as_int(r) for r in as_list(raw.get("runes"))),
        spells=tuple(as_int(s) for s in as_list(raw.get("spells"))),
        rune_options=tuple(
            tuple(as_int(n) for n in option)
            for option in as_list(raw.get("rune_options"))
            if isinstance(option, list)
        ),
        matchups=tuple(
            (as_int(row[0]), as_int(row[1]), as_int(row[2]))
            for row in as_list(raw.get("matchups"))
            if isinstance(row, list) and len(row) == 3
        ),
    )
