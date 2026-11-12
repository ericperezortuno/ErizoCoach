"""Muestra en la consola lo que la aplicación detecta y hace."""

from __future__ import annotations

import logging

from ..bus import EventBus
from ..champselect.models import PlayerSlot
from ..events import (
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    LoadoutApplied,
    LoadoutReady,
    MyChampionChanged,
    PicksFinalized,
)
from ..gamedata.catalog import GameData
from ..gamedata.runes import PRIMARY_INDEXES, SECONDARY_INDEXES, SHARD_INDEXES, RunePage
from ..loadout.models import LANE_CONFIDENCE
from .labels import GAMEFLOW_LABELS, POSITION_LABELS

log = logging.getLogger("lolcoach.ui")


class ConsoleReporter:
    def __init__(self, bus: EventBus, gamedata: GameData) -> None:
        self._gamedata = gamedata
        self._last_state = ""
        bus.subscribe(ClientConnected, self._on_connected)
        bus.subscribe(ClientDisconnected, self._on_disconnected)
        bus.subscribe(GameflowPhaseChanged, self._on_phase)
        bus.subscribe(ChampSelectStarted, self._on_started)
        bus.subscribe(ChampionLocked, self._on_locked)
        bus.subscribe(MyChampionChanged, self._on_my_champion)
        bus.subscribe(PicksFinalized, self._on_finalized)
        bus.subscribe(LoadoutReady, self._on_loadout)
        bus.subscribe(LoadoutApplied, self._on_applied)
        bus.subscribe(ChampSelectEnded, self._on_ended)

    # --- Cliente y estado ---------------------------------------------------------

    def _on_connected(self, event: ClientConnected) -> None:
        info = event.info
        self._last_state = ""
        log.info(
            "Conectado al cliente de LoL (parche %s, servidor %s, idioma %s)",
            info.patch,
            info.region,
            info.locale,
        )

    def _on_disconnected(self, _event: ClientDisconnected) -> None:
        log.info("Se ha cerrado el cliente de LoL. Esperando a que vuelva a abrirse...")

    def _on_phase(self, event: GameflowPhaseChanged) -> None:
        label = GAMEFLOW_LABELS.get(event.current, "")
        # El cliente a veces alterna estados muy rápido (p. ej., al salir de la práctica):
        # solo mostramos un estado si es distinto del último mostrado.
        if label and label != self._last_state:
            self._last_state = label
            log.info("Estado: %s", label)

    # --- Selección de campeones -------------------------------------------------------

    def _on_started(self, event: ChampSelectStarted) -> None:
        name = event.queue.name if event.queue and event.queue.name else ""
        log.info("--- Selección de campeones%s ---", f" · {name}" if name else "")
        me = event.snapshot.me
        if me is not None and me.position:
            log.info("  Tu posición: %s", POSITION_LABELS[me.position])

    def _on_locked(self, event: ChampionLocked) -> None:
        slot = event.slot
        if slot.is_local_player:
            who = "Has bloqueado"
        else:
            who = "Aliado ha bloqueado" if slot.is_ally else "Enemigo ha bloqueado"
        log.info("  %s: %s", who, self._describe(slot))

    def _on_my_champion(self, event: MyChampionChanged) -> None:
        if event.previous_champion_id:  # el primer bloqueo ya se muestra en _on_locked
            log.info(
                "  Tu campeón ha cambiado: %s -> %s",
                self._gamedata.champion_name(event.previous_champion_id),
                self._gamedata.champion_name(event.champion_id),
            )

    def _on_finalized(self, event: PicksFinalized) -> None:
        snapshot = event.snapshot
        me = snapshot.me
        log.info("Picks completados:")
        log.info("  Tú:       %s", self._describe(me) if me else "?")
        allies = [self._describe(s) for s in snapshot.allies if s.has_champion]
        log.info("  Aliados:  %s", ", ".join(allies) if allies else "-")
        if snapshot.enemies_known:
            enemies = [self._describe(s) for s in snapshot.known_enemies]
            log.info("  Enemigos: %s", ", ".join(enemies))
        else:
            log.info("  Enemigos: no visibles (selección a ciegas o partida de práctica)")

    def _on_ended(self, event: ChampSelectEnded) -> None:
        if event.outcome == ChampSelectOutcome.GAME_STARTING:
            log.info("La partida está empezando. ¡Suerte!")
        else:
            log.info("Selección de campeones cancelada.")

    # --- Runas y hechizos ---------------------------------------------------------------

    def _on_loadout(self, event: LoadoutReady) -> None:
        loadout = event.loadout
        gd = self._gamedata
        position = POSITION_LABELS.get(loadout.position, "")
        champion = gd.champion_name(loadout.champion_id)
        log.info("Recomendación para %s%s:", champion, f" ({position})" if position else "")
        if loadout.base is not None and loadout.base.descriptor:
            log.info("  Base: recomendación de Riot «%s»", loadout.base.descriptor)
        if loadout.runes is not None:
            for line in self._rune_lines(loadout.runes):
                log.info("  %s", line)
        if loadout.spells is not None:
            log.info("  Hechizos: %s", " + ".join(gd.spell_name(s) for s in loadout.spells))
        guess = loadout.lane_guess
        if guess is not None and guess.probability >= LANE_CONFIDENCE:
            log.info(
                "  Rival de línea probable: %s (%d %%)",
                gd.champion_name(guess.champion_id),
                round(guess.probability * 100),
            )
        for adjustment in loadout.adjustments:
            log.info("  Ajuste: %s", adjustment)
        for note in loadout.notes:
            log.info("  Nota: %s", note)

    def _on_applied(self, event: LoadoutApplied) -> None:
        done = []
        if event.runes_applied:
            done.append(f"runas en la página «{event.page_name}»")
        if event.spells_applied:
            done.append("hechizos")
        if done:
            log.info(
                "Aplicado: %s. Puedes cambiarlos a mano; no se volverán a tocar.", " y ".join(done)
            )
        for problem in event.problems:
            log.warning(problem)

    def _rune_lines(self, page: RunePage) -> list[str]:
        gd = self._gamedata

        def names(indexes: tuple[int, ...]) -> str:
            return ", ".join(gd.perk_name(page.perk_ids[i]) for i in indexes)

        return [
            f"Runas: {gd.perk_name(page.keystone_id)} ({gd.style_name(page.primary_style_id)}): "
            f"{names(PRIMARY_INDEXES)}",
            f"       {gd.style_name(page.sub_style_id)}: {names(SECONDARY_INDEXES)}",
            f"       Fragmentos: {names(SHARD_INDEXES)}",
        ]

    def _describe(self, slot: PlayerSlot) -> str:
        name = self._gamedata.champion_name(slot.champion_id)
        position = POSITION_LABELS.get(slot.position, "")
        return f"{name} ({position})" if position else name
