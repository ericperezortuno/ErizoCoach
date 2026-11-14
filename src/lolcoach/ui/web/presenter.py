"""Convierte los eventos de la app en el estado que muestra la ventana (JSON).

La ventana no calcula nada: solo pinta este estado. Así, la lógica sigue en un
único sitio y la interfaz se puede cambiar (o rehacer) sin tocarla.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

from ... import __version__
from ...builds.models import ARCHETYPE_LABELS, BuildPlan
from ...bus import EventBus
from ...events import (
    BuildReady,
    ChampSelectStarted,
    ClientConnected,
    ClientDisconnected,
    CountersUpdated,
    GameflowPhaseChanged,
    LiveGameEnded,
    LiveGameUpdated,
    LoadoutInClient,
    LoadoutPreview,
    LoadoutReady,
    PicksFinalized,
    RunesBy,
    RunesEditRequested,
    SpellsEditRequested,
    StatsStatus,
)
from ...gamedata.catalog import DamageType, GameData
from ...gamedata.runes import (
    PAGE_SIZE,
    PRIMARY_INDEXES,
    SECONDARY_INDEXES,
    SHARD_INDEXES,
    RunePage,
)
from ...ingame.analysis import STRENGTH_LABELS, GameReport, PlayerReport
from ...ingame.models import LivePlayer
from ...loadout.models import LANE_CONFIDENCE, CounterPick, Loadout
from ...loadout.rules import SMITE, SUMMONERS_RIFT, smite_rule
from ...models import LANE_POSITIONS, GameflowPhase, Position
from ..labels import GAMEFLOW_LABELS, POSITION_LABELS

State = dict[str, Any]
Listener = Callable[[State], Awaitable[None]]

RUNES_STATUS = {
    RunesBy.PREVIEW: (
        "Puestas en tu cliente al mantener la preselección; al último pick se ajustan "
        "a los rivales."
    ),
    RunesBy.COACH: "Puestas en tu cliente.",
    RunesBy.YOU_IN_APP: (
        "Tus cambios están puestos en el cliente. LoL Coach ya no las toca para este campeón."
    ),
    RunesBy.YOU_IN_CLIENT: (
        "Las has cambiado en el cliente: LoL Coach no las toca para este campeón."
    ),
}
PREVIEW_STATUS = "Previsión: si te quedas 5 segundos en este campeón, se ponen en tu cliente."

DAMAGE_LABELS = {
    DamageType.MAGIC: "AP",
    DamageType.PHYSICAL: "AD",
    DamageType.MIXED: "Mixto",
    DamageType.UNKNOWN: "",
}


class UiPresenter:
    def __init__(self, bus: EventBus, gamedata: GameData) -> None:
        self._bus = bus
        self._gd = gamedata
        self._listeners: list[Listener] = []
        self._shown_plan: BuildPlan | None = None  # la build que enseña la tarjeta
        self._loadout_final = False  # ya están las runas de verdad: fuera previsiones
        self._loadout: Loadout | None = None  # la recomendación que se enseña
        self._loadout_preview = False
        self._in_client: LoadoutInClient | None = None  # lo que hay en el cliente
        self._catalog: tuple[str | None, State] | None = None  # (parche, árboles de runas)
        self._state: State = {
            "version": __version__,
            "connected": False,
            "phase": GameflowPhase.UNKNOWN.value,
            "phaseLabel": "Esperando al cliente de LoL",
            "champion": None,
            "loadout": None,
            "build": None,
            "game": None,
            "stats": None,  # estado de la descarga automática de estadísticas
            "counters": None,  # counters contra tu rival de línea (selección)
            "runeCatalog": None,  # árboles de runas, para cambiarlas desde la ventana
            "spellCatalog": [],  # hechizos que se pueden elegir en este modo
            "spellRule": "",  # Aplastar: "required" (jungla), "forbidden" (otra posición) o libre
            "smiteId": SMITE,
        }
        bus.subscribe(ClientConnected, self._on_connected)
        bus.subscribe(ClientDisconnected, self._on_disconnected)
        bus.subscribe(GameflowPhaseChanged, self._on_phase)
        bus.subscribe(ChampSelectStarted, self._on_champ_select)
        bus.subscribe(PicksFinalized, self._on_finalized)
        bus.subscribe(LoadoutPreview, self._on_preview)
        bus.subscribe(LoadoutReady, self._on_loadout)
        bus.subscribe(LoadoutInClient, self._on_in_client)
        bus.subscribe(BuildReady, self._on_build)
        bus.subscribe(LiveGameUpdated, self._on_live)
        bus.subscribe(LiveGameEnded, self._on_live_ended)
        bus.subscribe(StatsStatus, self._on_stats)
        bus.subscribe(CountersUpdated, self._on_counters)

    @property
    def state(self) -> State:
        return self._state

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def show_label(self, label: str) -> None:
        """Cambia el texto de estado (p. ej., en el modo demostración)."""
        await self._set(phaseLabel=label)

    async def _set(self, **changes: Any) -> None:
        self._state = {**self._state, **changes}
        for listener in list(self._listeners):
            await listener(self._state)

    # --- Cliente y estado -----------------------------------------------------------

    async def _on_connected(self, _event: ClientConnected) -> None:
        await self._set(connected=True, phaseLabel="Conectado")

    async def _on_disconnected(self, _event: ClientDisconnected) -> None:
        await self._set(
            connected=False, phase=GameflowPhase.UNKNOWN.value,
            phaseLabel="Esperando al cliente de LoL", game=None,
        )  # fmt: skip

    async def _on_phase(self, event: GameflowPhaseChanged) -> None:
        label = GAMEFLOW_LABELS.get(event.current) or self._state["phaseLabel"]
        await self._set(phase=event.current.value, phaseLabel=label)

    async def _on_champ_select(self, event: ChampSelectStarted) -> None:
        self._shown_plan = None
        self._loadout_final = False
        self._loadout, self._in_client = None, None
        mode = event.queue.game_mode if event.queue else ""
        spells = [self._spell(s.id) for s in self._gd.spells_for(mode)]
        me = event.snapshot.me
        map_id = event.queue.map_id if event.queue and event.queue.map_id else SUMMONERS_RIFT
        rule = smite_rule(me.position if me else Position.NONE, map_id)
        # Los counters no se borran aquí: el módulo de counters ya ha publicado los de esta
        # selección (se le avisa antes; si ya había rival de línea, se perderían).
        await self._set(
            champion=None, loadout=None, build=None, game=None,
            spellCatalog=spells, spellRule=rule.value, smiteId=SMITE,
        )  # fmt: skip

    async def _on_finalized(self, event: PicksFinalized) -> None:
        me = event.snapshot.me
        if me is not None and me.has_champion:
            await self._set(champion=self._champion(me.champion_id, POSITION_LABELS[me.position]))

    # --- Runas y build ----------------------------------------------------------------

    async def _on_counters(self, event: CountersUpdated) -> None:
        """Counters contra tu rival de línea (o cómo va tu enfrentamiento si ya has elegido)."""
        if not event.enemy_id or (not event.picks and event.mine is None):
            await self._set(counters=None)
            return

        def pick(p: CounterPick) -> State:
            return {
                "id": p.champion_id,
                "name": self._gd.champion_name(p.champion_id),
                "winRate": round(p.win_rate * 100),
                "games": p.games,
                "wins": p.wins,
            }

        await self._set(
            counters={
                "enemy": self._champion(event.enemy_id, POSITION_LABELS.get(event.position, "")),
                "picks": [pick(p) for p in event.picks],
                "mine": pick(event.mine) if event.mine else None,
                "locked": event.locked,
            }
        )

    async def _on_stats(self, event: StatsStatus) -> None:
        await self._set(stats={"text": event.text, "problem": event.problem})

    async def _on_preview(self, event: LoadoutPreview) -> None:
        if self._loadout_final:
            return
        loadout = event.loadout
        if not event.champion_id:
            self._loadout = None
            await self._set(champion=None, loadout=None)
            return
        position = POSITION_LABELS.get(loadout.position, "") if loadout else ""
        shown = loadout is not None and (loadout.runes is not None or loadout.spells is not None)
        self._loadout = loadout if shown else None
        self._loadout_preview = True
        await self._set(
            champion=self._champion(event.champion_id, position) | {"preview": True},
            loadout=self._loadout_state(),
            runeCatalog=self._rune_catalog(),
        )

    async def _on_loadout(self, event: LoadoutReady) -> None:
        self._loadout_final = True
        self._loadout, self._loadout_preview = event.loadout, False
        await self._set(loadout=self._loadout_state(), runeCatalog=self._rune_catalog())

    async def _on_in_client(self, event: LoadoutInClient) -> None:
        self._in_client = event
        await self._set(loadout=self._loadout_state())

    async def request_runes(self, raw: Any) -> bool:
        """La ventana pide poner otra página de runas. Solo acepta números; la página se
        vuelve a validar con los árboles del juego antes de escribirla en el cliente."""
        page = _parse_page(raw)
        if page is None:
            return False
        await self._bus.publish(RunesEditRequested(page))
        return True

    async def request_spells(self, raw: Any) -> bool:
        """La ventana pide otros hechizos: ``{"spell1Id": n, "spell2Id": n}`` (D y F). Se
        comprueba después que existan en este modo antes de ponerlos."""
        if not isinstance(raw, dict):
            return False
        first, second = raw.get("spell1Id"), raw.get("spell2Id")
        if not all(type(n) is int and 0 < n < MAX_ID for n in (first, second)) or first == second:
            return False
        await self._bus.publish(SpellsEditRequested((int(first), int(second))))  # type: ignore[arg-type]
        return True

    async def _on_build(self, event: BuildReady) -> None:
        plan = event.plan
        self._shown_plan = plan
        await self._set(
            build=self._build(plan),
            champion=self._champion(plan.champion_id, POSITION_LABELS.get(plan.position, "")),
        )

    def _loadout_state(self) -> State | None:
        """La tarjeta de runas: lo que hay en tu cliente si ya se ha puesto algo; si no, la
        recomendación (previsión)."""
        loadout = self._loadout
        if loadout is None:
            return None
        gd = self._gd
        here = self._in_client
        if here is not None and here.champion_id != loadout.champion_id:
            here = None  # es de otro campeón que mirabas antes
        runes = here.runes if here is not None and here.runes is not None else loadout.runes
        spells = here.spells if here is not None and here.spells is not None else loadout.spells
        runes_by = here.runes_by if here is not None else RunesBy.NOBODY
        guess = loadout.lane_guess
        status = RUNES_STATUS.get(runes_by, PREVIEW_STATUS if self._loadout_preview else "")
        if here is not None and here.page_name and runes_by.value:
            status = f"{status} Página «{here.page_name}»."
        state: State = {
            "page": self._page(runes) if runes else None,
            "spells": [self._spell(s) for s in (spells or ())],
            "runesBy": runes_by.value,
            "spellsBy": here.spells_by.value if here is not None else "",
            "status": status,
            "problem": here.problem if here is not None else "",
            "editable": runes is not None,
            "options": [
                {
                    "page": self._page(option.page),
                    "detail": option.detail,
                    "recommended": option.recommended,
                }
                for option in loadout.options
            ],
            "laneOpponent": (
                f"{gd.champion_name(guess.champion_id)} ({round(guess.probability * 100)} %)"
                if guess and guess.probability >= LANE_CONFIDENCE
                else None
            ),
            "adjustments": list(loadout.adjustments),
            "notes": list(loadout.notes),
            "source": loadout.source,
        }
        if self._loadout_preview:
            state["preview"] = True
        return state

    def _perk(self, perk_id: int) -> State:
        return {"id": perk_id, "name": self._gd.perk_name(perk_id)}

    def _spell(self, spell_id: int) -> State:
        return {"id": spell_id, "name": self._gd.spell_name(spell_id)}

    def _style(self, style_id: int) -> State:
        return {"id": style_id, "name": self._gd.style_name(style_id)}

    def _page(self, runes: RunePage) -> State:
        perks = runes.perk_ids
        complete = len(perks) == PAGE_SIZE
        return {
            "primaryStyle": self._style(runes.primary_style_id),
            "subStyle": self._style(runes.sub_style_id),
            "keystone": self._perk(runes.keystone_id),
            "primary": [self._perk(perks[i]) for i in PRIMARY_INDEXES] if complete else [],
            "secondary": [self._perk(perks[i]) for i in SECONDARY_INDEXES] if complete else [],
            "shards": [self._perk(perks[i]) for i in SHARD_INDEXES] if complete else [],
            "perkIds": list(perks),
        }

    def _rune_catalog(self) -> State | None:
        """Los árboles de runas (para cambiarlas en la ventana); una vez por parche."""
        version = self._gd.version
        if self._catalog is not None and self._catalog[0] == version:
            return self._catalog[1]
        styles = sorted(
            (s for s in self._gd.styles.values() if len(s.rows) == 4 and len(s.shard_rows) == 3),
            key=lambda s: s.id,
        )
        if not styles:
            return None
        catalog: State = {
            "styles": [
                {
                    "id": s.id,
                    "name": s.name,
                    "rows": [list(row) for row in s.rows],
                    "allowedSub": sorted(s.allowed_sub_styles),
                }
                for s in styles
            ],
            "shards": [list(row) for row in styles[0].shard_rows],
            "names": {
                str(perk): self._gd.perk_name(perk)
                for s in styles
                for row in (*s.rows, *s.shard_rows)
                for perk in row
            },
        }
        self._catalog = (version, catalog)
        return catalog

    def _build(self, plan: BuildPlan) -> State:
        item = self._item
        return {
            "archetype": ARCHETYPE_LABELS[plan.archetype],
            "source": plan.source,
            "starting": [item(s.item_id) | {"count": s.count} for s in plan.starting],
            "boots": item(plan.boots) if plan.boots is not None else None,
            "core": [item(i) for i in plan.core],
            "full": [item(i) for i in plan.full],
            "situational": [item(s.item_id) | {"reason": s.reason} for s in plan.situational],
            "adjustments": list(plan.adjustments),
            "notes": list(plan.notes),
        }

    # --- Partida en vivo --------------------------------------------------------------

    async def _on_live(self, event: LiveGameUpdated) -> None:
        game, report, advice = event.game, event.report, event.advice
        minutes, seconds = divmod(int(game.game_time), 60)
        state: State = {
            "time": f"{minutes}:{seconds:02d}",
            "gold": int(game.current_gold),
            "me": self._player(report.me) if report.me else None,
            # Por línea en los dos equipos: cada fila enfrenta a los rivales de esa línea.
            "enemies": [self._player(r) for r in _by_lane(report.enemies, game.enemies)],
            "allies": [self._player(r) for r in report.allies],
            "team": [
                self._player(r) | {"me": r is report.me}
                for r in _by_lane(
                    (report.me, *report.allies) if report.me else report.allies,
                    game.players,
                )
            ],
            "matchups": self._matchups(report, game.players),
            # Oro total de cada equipo, como en las retransmisiones de Riot
            "teamGold": {"mine": report.team_gold, "theirs": report.enemy_gold},
            "next": None,
            "message": "",
            "buildProgress": [],
        }
        if advice is not None:
            state["buildProgress"] = [
                self._item(s.item_id) | {"owned": s.owned} for s in advice.build
            ]
            state["message"] = advice.message
            purchase = advice.next_purchase
            if purchase is not None:
                state["next"] = self._item(purchase.item_id) | {
                    "reason": purchase.reason,
                    "remaining": purchase.remaining_gold,
                    "canBuy": purchase.can_buy_now,
                    "components": [self._item(c) for c in purchase.affordable_components],
                    "starting": [
                        self._item(s.item_id) | {"count": s.count} for s in purchase.starting
                    ],
                }
                if purchase.starting:  # el precio y el nombre son los del conjunto
                    starting = state["next"]["starting"]
                    state["next"]["price"] = sum(s["price"] * s["count"] for s in starting)
                    state["next"]["name"] = " + ".join(
                        f"{s['count']} × {s['name']}" if s["count"] > 1 else s["name"]
                        for s in starting
                    )
        changes: State = {"game": state}
        # Sin selección (app abierta a mitad) o recalculada (llegan las estadísticas)
        if event.plan is not None and event.plan is not self._shown_plan:
            self._shown_plan = event.plan
            changes["build"] = self._build(event.plan)
            changes["champion"] = self._champion(
                event.plan.champion_id, POSITION_LABELS.get(event.plan.position, "")
            )
        await self._set(**changes)

    async def _on_live_ended(self, _event: LiveGameEnded) -> None:
        await self._set(game=None)

    # --- Utilidades ---------------------------------------------------------------------

    def _item(self, item_id: int) -> State:
        item = self._gd.item(item_id)
        return {
            "id": item_id,
            "name": self._gd.item_name(item_id),
            "price": item.price_total if item else 0,
        }

    def _champion(self, champion_id: int, position: str) -> State:
        return {
            "id": champion_id,
            "name": self._gd.champion_name(champion_id),
            "position": position,
        }

    def _player(self, report: PlayerReport) -> State:
        p = report.player
        return {
            "championId": report.champion_id,
            "name": self._gd.champion_name(report.champion_id)
            if report.champion_id
            else p.champion_name,
            "kda": p.kda,
            "cs": p.creep_score,
            "level": p.level,
            "itemGold": report.item_gold,
            "totalGold": report.gold,
            "goldDiff": report.gold_vs_average,
            # Frente a su rival de posición (el de su línea en el otro equipo)
            "laneGoldDiff": report.gold_vs_lane_rival,
            "laneRival": self._gd.champion_name(report.lane_rival_id)
            if report.lane_rival_id
            else "",
            "strength": report.strength.value,
            "strengthLabel": STRENGTH_LABELS[report.strength],
            "damage": DAMAGE_LABELS[report.damage_type],
            "lane": report.is_lane_opponent,
            "position": POSITION_LABELS.get(report.position, ""),
            "dead": p.is_dead,
            "respawn": round(p.respawn_timer),
            "items": [i.item_id for i in p.items],
        }

    def _matchups(self, report: GameReport, order: Sequence[LivePlayer]) -> list[State]:
        """Una fila por línea: tu compañero, la diferencia de oro y su rival de posición.

        La diferencia es desde tu equipo (positiva = tu compañero lleva más oro).
        Vacío si nadie tiene rival de posición (otros modos): entonces van en dos listas."""
        mine = _by_lane((report.me, *report.allies) if report.me else report.allies, order)
        theirs = _by_lane(report.enemies, order)
        if not any(r.lane_rival_id for r in mine):
            return []
        rows: list[State] = []
        paired: set[int] = set()
        for ally in mine:
            rival = next(
                (e for e in theirs if ally.lane_rival_id and e.champion_id == ally.lane_rival_id),
                None,
            )
            if rival is not None:
                paired.add(id(rival))
            rows.append(
                {
                    "position": POSITION_LABELS.get(ally.position, ""),
                    "ally": self._player(ally) | {"me": ally is report.me},
                    "enemy": self._player(rival) if rival else None,
                    "goldDiff": ally.gold_vs_lane_rival if rival else None,
                }
            )
        for enemy in theirs:  # rivales sin pareja (raro): al final, solos
            if id(enemy) not in paired:
                position = POSITION_LABELS.get(enemy.position, "")
                rows.append(
                    {
                        "position": position,
                        "ally": None,
                        "enemy": self._player(enemy),
                        "goldDiff": None,
                    }
                )
        return rows


_LANE_RANK = {position: rank for rank, position in enumerate(LANE_POSITIONS)}


def _by_lane(reports: Iterable[PlayerReport], order: Sequence[LivePlayer]) -> list[PlayerReport]:
    """Superior, jungla, medio, inferior y apoyo; sin línea conocida, al final y en el orden
    en que los da el juego."""
    seat = {id(player): index for index, player in enumerate(order)}
    return sorted(
        reports,
        key=lambda r: (
            _LANE_RANK.get(r.position, len(LANE_POSITIONS)),
            seat.get(id(r.player), len(seat)),
        ),
    )


MAX_ID = 100_000


def _parse_page(raw: Any) -> RunePage | None:
    """``{"primaryStyleId": n, "subStyleId": n, "perkIds": [9 números]}`` o None."""
    if not isinstance(raw, dict):
        return None
    perks = raw.get("perkIds")
    if not isinstance(perks, list) or len(perks) != PAGE_SIZE:
        return None
    primary, sub = raw.get("primaryStyleId"), raw.get("subStyleId")
    numbers = [primary, sub, *perks]
    # type() y no isinstance(): True/False no deben pasar por números
    if not all(type(n) is int and 0 < n < MAX_ID for n in numbers):
        return None
    return RunePage(int(primary), int(sub), tuple(int(p) for p in perks))  # type: ignore[arg-type]
