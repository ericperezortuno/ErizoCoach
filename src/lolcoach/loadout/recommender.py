"""Calcula las runas y hechizos para tu partida.

Pasos:

1. Tu posición: la asignada en la selección; si no hay (a ciegas, práctica), la
   más jugada en alto elo o, sin datos, la habitual de tu campeón.
2. Base: las runas y hechizos más jugados en alto elo para tu campeón y posición
   (si hay partidas suficientes); si no, la recomendación oficial de Riot.
3. Si se ven los enemigos: se estima la línea de cada uno y se aplican los
   ajustes de ``rules``. Si no, se queda la base (lo mejor por defecto para tu campeón).
4. Se valida la página antes de devolverla: nunca se envía una página rota al cliente.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from ..champselect.models import ChampSelectSnapshot
from ..gamedata.catalog import GameData
from ..gamedata.runes import (
    SECONDARY_INDEXES,
    PerkStyle,
    RecommendedPage,
    RunePage,
    validate_page,
)
from ..lcu.client import LcuClient
from ..models import Position, QueueInfo
from .models import LANE_CONFIDENCE, Loadout, LoadoutStats, MatchContext, RuneOption
from .positions import (
    SUMMONERS_RIFT,
    PositionHint,
    infer_positions,
    lane_opponent,
    resolve_my_position,
)
from .rules import adjust_runes, adjust_spells, keep_flash_key

log = logging.getLogger(__name__)

RUNE_OPTIONS = 3  # páginas para elegir en la ventana


class LoadoutRecommender:
    def __init__(
        self,
        gamedata: GameData,
        position_hint: PositionHint | None = None,
        stats: LoadoutStats | None = None,
    ) -> None:
        self._gamedata = gamedata
        self._position_hint = position_hint
        self._stats = stats

    def resolve_position(self, snapshot: ChampSelectSnapshot, map_id: int) -> Position:
        me = snapshot.me
        if me is None:
            return Position.NONE
        return resolve_my_position(
            me.position, me.champion_id, map_id, self._gamedata.positions_for, self._position_hint
        )

    async def prefetch(
        self, client: LcuClient, snapshot: ChampSelectSnapshot, queue: QueueInfo | None
    ) -> None:
        """Adelanta las descargas para que, al último pick, todo esté en caché."""
        me = snapshot.me
        if me is None or not me.has_champion:
            return
        map_id = _map_id(queue)
        await self._gamedata.recommended_pages(
            client, me.champion_id, self.resolve_position(snapshot, map_id), map_id
        )
        await self._gamedata.profile(client, me.champion_id)

    async def recommend(
        self, client: LcuClient, snapshot: ChampSelectSnapshot, queue: QueueInfo | None
    ) -> Loadout | None:
        me = snapshot.me
        if me is None or not me.has_champion:
            return None
        gd = self._gamedata
        map_id = _map_id(queue)
        game_mode = queue.game_mode if queue else ""
        position = self.resolve_position(snapshot, map_id)

        pages = await gd.recommended_pages(client, me.champion_id, position, map_id)
        if not pages:
            return Loadout(
                champion_id=me.champion_id,
                position=position,
                base=None,
                runes=None,
                spells=None,
                notes=("Este modo no tiene runas recomendadas: no se cambia nada.",),
            )
        base = _pick_base(pages, position)
        if position == Position.NONE and map_id == SUMMONERS_RIFT:
            position = base.position
        source = f"recomendación de Riot «{base.descriptor}»" if base.descriptor else ""
        if map_id == SUMMONERS_RIFT and (found := self._stats_base(me.champion_id, position)):
            base, source = found

        enemy_ids = [slot.champion_id for slot in snapshot.known_enemies]
        profiles = await gd.profiles(client, [me.champion_id, *enemy_ids])
        enemies = tuple(profiles[c] for c in enemy_ids if c in profiles)

        guess = None
        if enemies and map_id == SUMMONERS_RIFT and position != Position.NONE:
            guess = lane_opponent(infer_positions(enemy_ids, gd.positions_for), position)

        ctx = MatchContext(
            champion_id=me.champion_id,
            position=position,
            map_id=map_id,
            game_mode=game_mode,
            me=profiles.get(me.champion_id),
            enemies=enemies,
            lane_guess=guess,
            lane_opponent=profiles.get(guess.champion_id) if guess else None,
            current_spells=(me.spell1_id, me.spell2_id),
        )

        runes, rune_reasons = adjust_runes(base.page, ctx, gd)
        problems = validate_page(runes, gd.styles)
        if problems and runes != base.page:
            log.warning("Ajuste de runas descartado (%s); se usa la página base", problems)
            runes, rune_reasons = base.page, []
            problems = validate_page(runes, gd.styles)
        if problems:
            log.warning("La página base no es válida (%s): no se aplican runas", problems)
            runes = None

        spells: tuple[int, int] | None = None
        spell_reasons: list[str] = []
        if len(base.spells) == 2:
            adjusted, spell_reasons = adjust_spells((base.spells[0], base.spells[1]), ctx, gd)
            spells = keep_flash_key(adjusted, ctx.current_spells)

        return Loadout(
            champion_id=me.champion_id,
            position=position,
            base=base,
            runes=runes,
            spells=spells,
            lane_guess=guess,
            adjustments=(*rune_reasons, *spell_reasons),
            notes=_notes(ctx, gd),
            source=source,
            options=self._options(runes, pages, ctx, from_stats=source.startswith("estadísticas"))
            if runes is not None
            else (),
        )

    def _options(
        self,
        chosen: RunePage,
        pages: tuple[RecommendedPage, ...],
        ctx: MatchContext,
        *,
        from_stats: bool = False,
    ) -> tuple[RuneOption, ...]:
        """Hasta 3 páginas con piedras angulares distintas: la recomendada y las siguientes más
        jugadas en alto elo; si faltan, las recomendaciones de Riot. Todas con los mismos
        ajustes a los rivales y validadas."""
        gd = self._gamedata
        candidates: list[tuple[RunePage, str]] = []
        if ctx.on_summoners_rift and self._stats is not None:
            for found in self._stats.rune_options_for(ctx.champion_id, ctx.position):
                rate = round(100 * found.wins / found.games) if found.games else 0
                candidates.append(
                    (found.page, f"{found.games} partidas de alto elo · {rate} % de victorias")
                )
        mine = [p for p in pages if p.position == ctx.position] or list(pages)
        for recommended in mine:
            label = f" · {recommended.descriptor}" if recommended.descriptor else ""
            candidates.append((recommended.page, f"Recomendación de Riot{label}"))

        def key(page: RunePage) -> tuple[int, int]:
            return page.primary_style_id, page.keystone_id

        # El porqué de la recomendada: sus datos de alto elo si los hay; si sale de las
        # estadísticas sin ese detalle, se dice así (no «de Riot», que confundiría)
        first = next((d for p, d in candidates if key(p) == key(chosen)), "")
        if from_stats and not first.endswith("victorias"):
            first = "la más jugada en alto elo"
        elif first.startswith("Recomendación de Riot"):
            first = first.replace("Recomendación de Riot", "de Riot", 1)
        options = [RuneOption(chosen, f"Recomendada · {first}" if first else "Recomendada", True)]
        seen = {key(chosen)}
        for page, detail in candidates:
            if len(options) >= RUNE_OPTIONS or key(page) in seen:
                continue
            page = _secondary_in_row_order(page, gd.styles)
            adjusted, _ = adjust_runes(page, ctx, gd)
            if validate_page(adjusted, gd.styles):
                adjusted = page  # el ajuste no encaja en esta página: tal cual
            if validate_page(adjusted, gd.styles):
                continue  # p. ej., runas de un parche anterior
            seen.add(key(page))
            options.append(RuneOption(adjusted, detail))
        return tuple(options)

    def _stats_base(
        self, champion_id: int, position: Position
    ) -> tuple[RecommendedPage, str] | None:
        """Runas y hechizos de las partidas de alto elo, si hay datos suficientes y son válidos."""
        if self._stats is None:
            return None
        found = self._stats.loadout_for(champion_id, position)
        if found is None:
            return None
        gd = self._gamedata
        page = _secondary_in_row_order(found.page, gd.styles)
        problems = validate_page(page, gd.styles)
        if problems:  # p. ej., runas de un parche anterior que ya no existen
            log.debug("Runas de estadísticas descartadas (%s): se usa la de Riot", problems)
            return None
        base = RecommendedPage(
            page=page,
            spells=found.spells,
            position=position,
            descriptor="",
        )
        return base, f"estadísticas de {found.games} partidas de alto elo (parche {found.patch})"


def _map_id(queue: QueueInfo | None) -> int:
    return queue.map_id if queue and queue.map_id else SUMMONERS_RIFT


def _pick_base(pages: tuple[RecommendedPage, ...], position: Position) -> RecommendedPage:
    """La primera recomendación de Riot para tu posición (Riot las ordena por preferencia)."""
    return next((p for p in pages if p.position == position), pages[0])


def _secondary_in_row_order(page: RunePage, styles: Mapping[int, PerkStyle]) -> RunePage:
    """Las dos runas secundarias en el orden de sus filas, como las ordena el cliente."""
    sub = styles.get(page.sub_style_id)
    first, second = SECONDARY_INDEXES
    if sub is None or len(page.perk_ids) <= second:
        return page
    pair = sorted((page.perk_ids[first], page.perk_ids[second]), key=lambda p: sub.row_of(p) or 0)
    return page.with_perk(first, pair[0]).with_perk(second, pair[1])


def _notes(ctx: MatchContext, gd: GameData) -> tuple[str, ...]:
    if not ctx.enemies_known:
        return ("Enemigos no visibles: runas y hechizos recomendados por defecto para tu campeón.",)
    if not ctx.on_summoners_rift or ctx.position == Position.NONE:
        return ()
    if ctx.lane_guess is None or ctx.lane_guess.probability < LANE_CONFIDENCE:
        return ("No está claro quién es tu rival de línea: sin ajustes específicos de línea.",)
    return ()
