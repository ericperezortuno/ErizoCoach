"""Muestra en la consola lo que la aplicación detecta y hace."""

from __future__ import annotations

import asyncio
import logging

from ..builds.models import ARCHETYPE_LABELS
from ..bus import EventBus
from ..champselect.models import PlayerSlot
from ..events import (
    BuildApplied,
    BuildReady,
    ChampionLocked,
    ChampSelectEnded,
    ChampSelectOutcome,
    ChampSelectStarted,
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    LiveGameEnded,
    LiveGameUpdated,
    LoadoutApplied,
    LoadoutReady,
    MyChampionChanged,
    PicksFinalized,
)
from ..gamedata.catalog import GameData
from ..gamedata.runes import PRIMARY_INDEXES, SECONDARY_INDEXES, SHARD_INDEXES, RunePage
from ..ingame.analysis import Strength
from ..loadout.models import LANE_CONFIDENCE
from .labels import GAMEFLOW_LABELS, POSITION_LABELS

log = logging.getLogger("erizocoach.ui")


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
        bus.subscribe(BuildReady, self._on_build)
        bus.subscribe(BuildApplied, self._on_build_applied)
        bus.subscribe(ChampSelectEnded, self._on_ended)
        bus.subscribe(LiveGameUpdated, self._on_live)
        bus.subscribe(LiveGameEnded, self._on_live_ended)
        self._last_purchase = 0
        self._very_strong: set[int] = set()

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
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            return  # es Erizo Coach el que se está cerrando, no el cliente
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
        if loadout.source:
            log.info("  Base: %s", loadout.source)
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
        if done and event.provisional:
            log.info(
                "Preselección mantenida, aplicado: %s. Al último pick se ajustan a los rivales "
                "(salvo lo que cambies tú).",
                " y ".join(done),
            )
        elif done:
            log.info(
                "Aplicado: %s. Puedes cambiarlos a mano; no se volverán a tocar.", " y ".join(done)
            )
        for problem in event.problems:
            log.warning(problem)

    # --- Build ---------------------------------------------------------------------------

    def _on_build(self, event: BuildReady) -> None:
        plan = event.plan
        gd = self._gamedata

        def names(ids: tuple[int, ...], sep: str = ", ") -> str:
            return sep.join(gd.item_name(i) for i in ids)

        log.info(
            "Build para %s: %s (base por %s)",
            gd.champion_name(plan.champion_id),
            ARCHETYPE_LABELS[plan.archetype],
            plan.source,
        )
        start = ", ".join(
            gd.item_name(s.item_id) + (f" x{s.count}" if s.count > 1 else "") for s in plan.starting
        )
        log.info("  Inicio:   %s", start)
        if plan.boots is not None:
            log.info("  Botas:    %s", gd.item_name(plan.boots))
        log.info("  Núcleo:   %s", names(plan.core, " > "))
        log.info("  Completa: %s", names(plan.full))
        for adjustment in plan.adjustments:
            log.info("  Ajuste: %s", adjustment)
        extra = [s for s in plan.situational if s.item_id not in plan.full]
        for item in extra:
            log.info("  Si hace falta: %s", item.reason)
        for item in plan.situational:
            if item.component_id is not None:
                log.info(
                    "  Consejo: compra pronto %s para %s",
                    gd.item_name(item.component_id),
                    gd.item_name(item.item_id),
                )
        for note in plan.notes:
            log.info("  Nota: %s", note)

    def _on_build_applied(self, event: BuildApplied) -> None:
        if event.applied:
            log.info("Aplicado: build en la tienda, conjunto «%s».", event.set_title)
        for problem in event.problems:
            log.warning(problem)

    # --- Partida en vivo (solo cambios importantes; el detalle está en la ventana) --------

    def _on_live(self, event: LiveGameUpdated) -> None:
        gd = self._gamedata
        for enemy in event.report.enemies:
            key = enemy.champion_id or hash(enemy.player.champion_alias)
            if enemy.strength == Strength.VERY_STRONG and key not in self._very_strong:
                self._very_strong.add(key)
                log.info(
                    "En partida: %s va muy fuerte (%s, %+d de oro en objetos frente a la media)",
                    gd.champion_name(enemy.champion_id) or enemy.player.champion_name,
                    enemy.player.kda,
                    enemy.gold_vs_average,
                )
        advice = event.advice
        purchase = advice.next_purchase if advice else None
        if purchase is not None and purchase.item_id != self._last_purchase:
            self._last_purchase = purchase.item_id
            log.info(
                "En partida: compra ahora %s (%s)", gd.item_name(purchase.item_id), purchase.reason
            )

    def _on_live_ended(self, _event: LiveGameEnded) -> None:
        self._last_purchase = 0
        self._very_strong.clear()

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
