"""Convierte la sesión JSON de ``/lol-champ-select/v1/session`` en ``ChampSelectSnapshot``.

Reglas clave:

* Un jugador ha **bloqueado** su campeón cuando su acción de tipo ``pick``
  está completada. Mientras no, ``championId`` es solo lo que está mirando.
* Tras bloquear, el campeón real es el de ``myTeam``/``theirTeam`` (cambia si
  hay intercambios); la acción conserva el original.
* En modos sin acciones de pick (ARAM), el campeón asignado cuenta como bloqueado.
* Mientras miras un campeón sin bloquearlo, el cliente lo pone en ``championPickIntent``
  y en la acción, pero no en ``championId`` (comprobado con datos reales).

Privacidad: la sesión en bruto contiene tu Riot ID y credenciales del chat (JWT).
Nunca la escribas completa en los registros; el modelo solo guarda lo necesario.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from ..util import as_dict, as_int, as_list
from .models import ChampSelectPhase, ChampSelectSnapshot, PlayerSlot, Position


def parse_session(data: Any) -> ChampSelectSnapshot:
    if not isinstance(data, Mapping):
        raise ValueError("La sesión de selección de campeones no es un objeto JSON")

    actions = [
        action
        for group in as_list(data.get("actions"))
        for action in as_list(group)
        if isinstance(action, Mapping)
    ]
    pick_actions = [action for action in actions if action.get("type") == "pick"]
    picks_by_cell: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for action in pick_actions:
        picks_by_cell[as_int(action.get("actorCellId"), -1)].append(action)

    local_cell = as_int(data.get("localPlayerCellId"), -1)
    my_team = tuple(
        _parse_slot(raw, is_ally=True, local_cell=local_cell, picks_by_cell=picks_by_cell)
        for raw in as_list(data.get("myTeam"))
        if isinstance(raw, Mapping)
    )
    their_team = tuple(
        _parse_slot(raw, is_ally=False, local_cell=local_cell, picks_by_cell=picks_by_cell)
        for raw in as_list(data.get("theirTeam"))
        if isinstance(raw, Mapping)
    )
    ally_bans, enemy_bans = _parse_bans(actions, as_dict(data.get("bans")))

    return ChampSelectSnapshot(
        game_id=as_int(data.get("gameId")),
        phase=ChampSelectPhase.parse(as_dict(data.get("timer")).get("phase")),
        my_team=my_team,
        their_team=their_team,
        ally_bans=ally_bans,
        enemy_bans=enemy_bans,
        has_pick_actions=bool(pick_actions),
        all_picks_completed=bool(pick_actions)
        and all(bool(action.get("completed")) for action in pick_actions),
        bench_enabled=bool(data.get("benchEnabled")),
    )


def _parse_slot(
    raw: Mapping[str, Any],
    *,
    is_ally: bool,
    local_cell: int,
    picks_by_cell: Mapping[int, list[Mapping[str, Any]]],
) -> PlayerSlot:
    cell_id = as_int(raw.get("cellId"), -1)
    champion_id = max(as_int(raw.get("championId")), 0)
    actions = picks_by_cell.get(cell_id, [])
    completed = [a for a in actions if a.get("completed") and as_int(a.get("championId")) > 0]
    if actions:
        locked = bool(completed)
        if locked and champion_id == 0:
            champion_id = as_int(completed[-1].get("championId"))
    else:
        locked = champion_id > 0  # campeón asignado (ARAM y similares)

    return PlayerSlot(
        cell_id=cell_id,
        is_ally=is_ally,
        is_local_player=is_ally and cell_id == local_cell,
        champion_id=champion_id,
        locked=locked,
        position=Position.parse(raw.get("assignedPosition")),
        spell1_id=max(as_int(raw.get("spell1Id")), 0),
        spell2_id=max(as_int(raw.get("spell2Id")), 0),
    )


def _parse_bans(
    actions: list[Mapping[str, Any]], bans: Mapping[str, Any]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    ally: list[int] = []
    enemy: list[int] = []
    for action in actions:
        champion_id = as_int(action.get("championId"))
        if action.get("type") != "ban" or not action.get("completed") or champion_id <= 0:
            continue
        (ally if action.get("isAllyAction") else enemy).append(champion_id)
    if not ally and not enemy:  # algunos modos solo lo informan aquí
        ally = [c for c in map(as_int, as_list(bans.get("myTeamBans"))) if c > 0]
        enemy = [c for c in map(as_int, as_list(bans.get("theirTeamBans"))) if c > 0]
    return tuple(ally), tuple(enemy)
