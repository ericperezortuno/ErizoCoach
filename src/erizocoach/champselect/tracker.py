"""Sigue la selección de campeones y emite eventos solo cuando algo cambia de verdad."""

from __future__ import annotations

import logging

from ..bus import EventBus
from ..events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    ChampSelectUpdated,
    MyChampionChanged,
    MyPickPreviewChanged,
    PicksFinalized,
)
from ..models import GameflowPhase, QueueInfo
from .models import ChampSelectSnapshot

log = logging.getLogger(__name__)


class ChampSelectTracker:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._reset()

    def _reset(self) -> None:
        self._snapshot: ChampSelectSnapshot | None = None
        self._queue: QueueInfo | None = None
        self._locked: set[tuple[bool, int]] = set()
        self._my_champion = 0
        self._my_preview = 0
        self._finalized = False

    @property
    def active(self) -> bool:
        return self._snapshot is not None

    @property
    def snapshot(self) -> ChampSelectSnapshot | None:
        return self._snapshot

    @property
    def queue(self) -> QueueInfo | None:
        return self._queue

    def set_queue(self, queue: QueueInfo | None) -> None:
        self._queue = queue

    async def update(self, snapshot: ChampSelectSnapshot) -> None:
        previous = self._snapshot
        if snapshot == previous:
            return  # solo ha cambiado el temporizador
        if (
            previous is not None
            and snapshot.game_id
            and previous.game_id
            and snapshot.game_id != previous.game_id
        ):
            log.debug("Nueva selección de campeones sin cierre de la anterior")
            self._reset()
            previous = None

        self._snapshot = snapshot
        if previous is None:
            await self._bus.publish(ChampSelectStarted(snapshot, self._queue))

        for slot in snapshot.slots:
            key = (slot.is_ally, slot.cell_id)
            if slot.locked and slot.has_champion and key not in self._locked:
                self._locked.add(key)
                await self._bus.publish(ChampionLocked(slot, snapshot))

        me = snapshot.me
        if me is not None and me.locked and me.has_champion and me.champion_id != self._my_champion:
            previous_champion, self._my_champion = self._my_champion, me.champion_id
            await self._bus.publish(MyChampionChanged(me.champion_id, previous_champion, snapshot))

        if me is not None and not self._finalized and not snapshot.is_finalized:
            shown = me.shown_champion
            if shown != self._my_preview:
                self._my_preview = shown
                await self._bus.publish(MyPickPreviewChanged(shown, snapshot))

        if snapshot.is_finalized and not self._finalized:
            self._finalized = True
            await self._bus.publish(PicksFinalized(snapshot, self._queue))

        await self._bus.publish(ChampSelectUpdated(snapshot))

    async def end(self, phase: GameflowPhase) -> None:
        """El cliente ha salido de la selección de campeones hacia ``phase``."""
        snapshot, queue = self._snapshot, self._queue
        self._reset()
        if snapshot is None:
            return
        outcome = (
            ChampSelectOutcome.GAME_STARTING
            if phase.is_game_starting
            else ChampSelectOutcome.CANCELLED
        )
        await self._bus.publish(ChampSelectEnded(outcome, snapshot, queue))

    def discard(self) -> None:
        """Olvida el estado sin emitir nada (p. ej., el cliente se ha cerrado)."""
        self._reset()
