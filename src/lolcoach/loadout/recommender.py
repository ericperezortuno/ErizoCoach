"""Calcula las runas y hechizos para tu partida.

Pasos:

1. Tu posición: la asignada en la selección; si no hay (a ciegas, práctica), la
   habitual de tu campeón.
2. Base: la recomendación oficial de Riot para tu campeón, posición y mapa.
3. Si se ven los enemigos: se estima la línea de cada uno y se aplican los
   ajustes de ``rules``. Si no, se queda la base (lo mejor por defecto para tu campeón).
4. Se valida la página antes de devolverla: nunca se envía una página rota al cliente.
"""

from __future__ import annotations

import logging

from ..champselect.models import ChampSelectSnapshot
from ..gamedata.catalog import GameData
from ..gamedata.runes import RecommendedPage, validate_page
from ..lcu.client import LcuClient
from ..models import Position, QueueInfo
from .models import LANE_CONFIDENCE, Loadout, MatchContext
from .positions import (
    SUMMONERS_RIFT,
    PositionHint,
    infer_positions,
    lane_opponent,
    resolve_my_position,
)
from .rules import adjust_runes, adjust_spells, keep_flash_key

log = logging.getLogger(__name__)


class LoadoutRecommender:
    def __init__(self, gamedata: GameData, position_hint: PositionHint | None = None) -> None:
        self._gamedata = gamedata
        self._position_hint = position_hint

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
            log.warning(
                "Ajuste de runas descartado (%s); se usa la recomendación de Riot", problems
            )
            runes, rune_reasons = base.page, []
            problems = validate_page(runes, gd.styles)
        if problems:
            log.warning("La recomendación de Riot no es válida (%s): no se aplican runas", problems)
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
        )


def _map_id(queue: QueueInfo | None) -> int:
    return queue.map_id if queue and queue.map_id else SUMMONERS_RIFT


def _pick_base(pages: tuple[RecommendedPage, ...], position: Position) -> RecommendedPage:
    """La primera recomendación de Riot para tu posición (Riot las ordena por preferencia)."""
    return next((p for p in pages if p.position == position), pages[0])


def _notes(ctx: MatchContext, gd: GameData) -> tuple[str, ...]:
    if not ctx.enemies_known:
        return ("Enemigos no visibles: runas y hechizos recomendados por defecto para tu campeón.",)
    if not ctx.on_summoners_rift or ctx.position == Position.NONE:
        return ()
    if ctx.lane_guess is None or ctx.lane_guess.probability < LANE_CONFIDENCE:
        return ("No está claro quién es tu rival de línea: sin ajustes específicos de línea.",)
    return ()
