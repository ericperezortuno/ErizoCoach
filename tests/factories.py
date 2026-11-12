"""Generadores de sesiones de selección de campeones con la forma real del cliente."""

from __future__ import annotations

from typing import Any

# Campeones de ejemplo (IDs reales)
AHRI, GAREN, LEE_SIN, JINX, THRESH = 103, 86, 64, 222, 412
ZED, DARIUS, KAISA, NAUTILUS, GRAVES = 238, 122, 145, 111, 104
YASUO, TEEMO, LUX, MORGANA = 157, 17, 99, 25
LEBLANC, LEONA, ASHE, MALPHITE, AMUMU, AKALI, KATARINA = 7, 89, 22, 54, 32, 84, 55

POSITIONS = ["top", "jungle", "middle", "bottom", "utility"]


def player(
    cell: int,
    champion: int = 0,
    *,
    position: str = "",
    intent: int = 0,
    team: int = 1,
    spells: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    return {
        "assignedPosition": position,
        "cellId": cell,
        "championId": champion,
        "championPickIntent": intent,
        "nameVisibilityType": "HIDDEN",
        "obfuscatedPuuid": "",
        "obfuscatedSummonerId": 0,
        "puuid": f"puuid-{cell}",
        "selectedSkinId": champion * 1000,
        "spell1Id": spells[0],
        "spell2Id": spells[1],
        "summonerId": 1000 + cell,
        "team": team,
        "wardSkinId": -1,
    }


def action(
    action_id: int,
    cell: int,
    kind: str,
    champion: int = 0,
    *,
    completed: bool = False,
    ally: bool = True,
    in_progress: bool = False,
) -> dict[str, Any]:
    return {
        "actorCellId": cell,
        "championId": champion,
        "completed": completed,
        "id": action_id,
        "isAllyAction": ally,
        "isInProgress": in_progress,
        "pickTurn": 0,
        "type": kind,
    }


def session(
    *,
    my_team: list[dict[str, Any]],
    their_team: list[dict[str, Any]] | None = None,
    actions: list[list[dict[str, Any]]] | None = None,
    local_cell: int = 2,
    phase: str = "BAN_PICK",
    game_id: int = 7_000_000_001,
    bench_enabled: bool = False,
    custom: bool = False,
) -> dict[str, Any]:
    return {
        "actions": actions or [],
        "allowBattleBoost": False,
        "allowDuplicatePicks": False,
        "allowRerolling": bench_enabled,
        "allowSkinSelection": True,
        "bans": {"myTeamBans": [], "numBans": 10, "theirTeamBans": []},
        "benchChampions": [],
        "benchEnabled": bench_enabled,
        "chatDetails": {},
        "counter": 1,
        "gameId": game_id,
        "hasSimultaneousBans": True,
        "hasSimultaneousPicks": False,
        "isCustomGame": custom,
        "isSpectating": False,
        "localPlayerCellId": local_cell,
        "lockedEventIndex": -1,
        "myTeam": my_team,
        "pickOrderSwaps": [],
        "recoveryCounter": 0,
        "rerollsRemaining": 0,
        "skipChampionSelect": False,
        "theirTeam": their_team or [],
        "timer": {
            "adjustedTimeLeftInPhase": 25_000,
            "internalNowInEpochMs": 1_700_000_000_000,
            "isInfinite": False,
            "phase": phase,
            "totalTimeInPhase": 30_000,
        },
        "trades": [],
    }


class DraftScenario:
    """Clasificatoria en draft: 10 baneos y picks en orden 1-2-2-2-2-1.

    Tú eres la celda 2 (medio) del equipo azul. Los enemigos son las celdas 5-9.
    """

    ALLY_CHAMPS = [GAREN, LEE_SIN, AHRI, JINX, THRESH]
    ENEMY_CHAMPS = [DARIUS, GRAVES, ZED, KAISA, NAUTILUS]
    ALLY_BANS = [YASUO, TEEMO, LUX, MORGANA, 1]
    ENEMY_BANS = [2, 3, 4, 5, 6]
    # (celda, es_aliado) en el orden real de picks
    PICK_ORDER = [
        (0, True),
        (5, False),
        (6, False),
        (1, True),
        (2, True),
        (7, False),
        (8, False),
        (3, True),
        (4, True),
        (9, False),
    ]

    def __init__(self, game_id: int = 7_000_000_001) -> None:
        self.game_id = game_id

    def state(self, picks_done: int, *, hover: int = 0, phase: str | None = None) -> dict[str, Any]:
        """Sesión tras ``picks_done`` picks (los baneos ya están hechos).

        ``hover``: campeón que el jugador local está mirando sin bloquear (si aún no le tocó).
        """
        done = set(self.PICK_ORDER[:picks_done])
        my_team = []
        for cell in range(5):
            champion = self.ALLY_CHAMPS[cell] if (cell, True) in done else 0
            if cell == 2 and not champion:
                champion = hover
            my_team.append(player(cell, champion, position=POSITIONS[cell], spells=(4, 14)))
        their_team = [
            player(5 + i, self.ENEMY_CHAMPS[i] if (5 + i, False) in done else 0, team=2)
            for i in range(5)
        ]
        bans = [action(1 + i, i, "ban", self.ALLY_BANS[i], completed=True) for i in range(5)] + [
            action(6 + i, 5 + i, "ban", self.ENEMY_BANS[i], completed=True, ally=False)
            for i in range(5)
        ]
        picks = []
        for n, (cell, ally) in enumerate(self.PICK_ORDER):
            champs = self.ALLY_CHAMPS if ally else self.ENEMY_CHAMPS
            idx = cell if ally else cell - 5
            completed = n < picks_done
            champion = champs[idx] if completed else (hover if cell == 2 else 0)
            picks.append(
                action(
                    100 + n,
                    cell,
                    "pick",
                    champion,
                    completed=completed,
                    ally=ally,
                    in_progress=n == picks_done,
                )
            )
        if phase is None:
            phase = "FINALIZATION" if picks_done >= len(self.PICK_ORDER) else "BAN_PICK"
        return session(
            my_team=my_team,
            their_team=their_team,
            actions=[bans, *[[p] for p in picks]],
            phase=phase,
            game_id=self.game_id,
        )


def blind_session(*, my_locked: bool, phase: str = "BAN_PICK") -> dict[str, Any]:
    """Normal a ciegas: picks simultáneos, enemigos invisibles."""
    my_team = [player(c, [GAREN, LEE_SIN, AHRI, JINX, THRESH][c]) for c in range(5)]
    their_team = [player(5 + i, 0, team=2) for i in range(5)]
    picks = [
        action(10 + c, c, "pick", my_team[c]["championId"], completed=(c != 2 or my_locked))
        for c in range(5)
    ]
    return session(my_team=my_team, their_team=their_team, actions=[picks], phase=phase)


def aram_session(my_champion: int, *, phase: str = "FINALIZATION") -> dict[str, Any]:
    """ARAM: campeones asignados, sin acciones de pick, con banquillo."""
    my_team = [player(c, [GAREN, LEE_SIN, my_champion, JINX, THRESH][c]) for c in range(5)]
    return session(my_team=my_team, their_team=[], actions=[], phase=phase, bench_enabled=True)
