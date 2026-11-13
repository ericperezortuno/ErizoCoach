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
    GameflowPhaseChanged,
    LiveGameEnded,
    LiveGameUpdated,
    LoadoutReady,
    PicksFinalized,
    StatsStatus,
)
from ...gamedata.catalog import DamageType, GameData
from ...gamedata.runes import PRIMARY_INDEXES, SECONDARY_INDEXES, SHARD_INDEXES
from ...ingame.analysis import STRENGTH_LABELS, PlayerReport
from ...ingame.models import LivePlayer
from ...loadout.models import LANE_CONFIDENCE, Loadout
from ...models import LANE_POSITIONS, GameflowPhase
from ..labels import GAMEFLOW_LABELS, POSITION_LABELS

State = dict[str, Any]
Listener = Callable[[State], Awaitable[None]]

DAMAGE_LABELS = {
    DamageType.MAGIC: "AP",
    DamageType.PHYSICAL: "AD",
    DamageType.MIXED: "Mixto",
    DamageType.UNKNOWN: "",
}


class UiPresenter:
    def __init__(self, bus: EventBus, gamedata: GameData) -> None:
        self._gd = gamedata
        self._listeners: list[Listener] = []
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
        }
        bus.subscribe(ClientConnected, self._on_connected)
        bus.subscribe(ClientDisconnected, self._on_disconnected)
        bus.subscribe(GameflowPhaseChanged, self._on_phase)
        bus.subscribe(ChampSelectStarted, self._on_champ_select)
        bus.subscribe(PicksFinalized, self._on_finalized)
        bus.subscribe(LoadoutReady, self._on_loadout)
        bus.subscribe(BuildReady, self._on_build)
        bus.subscribe(LiveGameUpdated, self._on_live)
        bus.subscribe(LiveGameEnded, self._on_live_ended)
        bus.subscribe(StatsStatus, self._on_stats)

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

    async def _on_champ_select(self, _event: ChampSelectStarted) -> None:
        await self._set(champion=None, loadout=None, build=None, game=None)

    async def _on_finalized(self, event: PicksFinalized) -> None:
        me = event.snapshot.me
        if me is not None and me.has_champion:
            await self._set(champion=self._champion(me.champion_id, POSITION_LABELS[me.position]))

    # --- Runas y build ----------------------------------------------------------------

    async def _on_stats(self, event: StatsStatus) -> None:
        await self._set(stats={"text": event.text, "problem": event.problem})

    async def _on_loadout(self, event: LoadoutReady) -> None:
        await self._set(loadout=self._loadout(event.loadout))

    async def _on_build(self, event: BuildReady) -> None:
        plan = event.plan
        await self._set(
            build=self._build(plan),
            champion=self._champion(plan.champion_id, POSITION_LABELS.get(plan.position, "")),
        )

    def _loadout(self, loadout: Loadout) -> State:
        gd = self._gd
        runes = loadout.runes
        guess = loadout.lane_guess
        return {
            "keystone": gd.perk_name(runes.keystone_id) if runes else None,
            "primaryStyle": gd.style_name(runes.primary_style_id) if runes else None,
            "primary": [gd.perk_name(runes.perk_ids[i]) for i in PRIMARY_INDEXES] if runes else [],
            "secondaryStyle": gd.style_name(runes.sub_style_id) if runes else None,
            "secondary": (
                [gd.perk_name(runes.perk_ids[i]) for i in SECONDARY_INDEXES] if runes else []
            ),
            "shards": [gd.perk_name(runes.perk_ids[i]) for i in SHARD_INDEXES] if runes else [],
            "spells": [{"id": s, "name": gd.spell_name(s)} for s in (loadout.spells or ())],
            "laneOpponent": (
                f"{gd.champion_name(guess.champion_id)} ({round(guess.probability * 100)} %)"
                if guess and guess.probability >= LANE_CONFIDENCE
                else None
            ),
            "adjustments": list(loadout.adjustments),
            "notes": list(loadout.notes),
            "source": loadout.source,
        }

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
                }
        changes: State = {"game": state}
        if event.plan is not None and self._state.get("build") is None:
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
            "goldDiff": report.gold_vs_average,
            "strength": report.strength.value,
            "strengthLabel": STRENGTH_LABELS[report.strength],
            "damage": DAMAGE_LABELS[report.damage_type],
            "lane": report.is_lane_opponent,
            "position": POSITION_LABELS.get(p.position, ""),
            "dead": p.is_dead,
            "respawn": round(p.respawn_timer),
            "items": [i.item_id for i in p.items],
        }


_LANE_RANK = {position: rank for rank, position in enumerate(LANE_POSITIONS)}


def _by_lane(reports: Iterable[PlayerReport], order: Sequence[LivePlayer]) -> list[PlayerReport]:
    """Superior, jungla, medio, inferior y apoyo; sin línea conocida, al final y en el orden
    en que los da el juego."""
    seat = {id(player): index for index, player in enumerate(order)}
    return sorted(
        reports,
        key=lambda r: (
            _LANE_RANK.get(r.player.position, len(LANE_POSITIONS)),
            seat.get(id(r.player), len(seat)),
        ),
    )
