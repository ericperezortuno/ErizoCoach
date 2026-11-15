"""Publica el paquete de estadísticas para quien no tiene clave de Riot (opcional).

Solo lo hace quien lo configura en ``config.toml``::

    stats_publish_repo = "git@github.com:<usuario>/LoLCoach-datos.git"
    stats_publish_key = "C:/Users/<usuario>/.ssh/lolcoach_datos_ed25519"

La clave SSH debe ser una «deploy key» con escritura **solo** en ese repositorio de
datos. Cada publicación sube un único commit nuevo (el repositorio no crece con el
tiempo) con el paquete y un README. Se publica como mucho cada pocas horas, y antes si
cambia el parche; nunca con pocas partidas.

Necesita ``git`` instalado. Si falla (sin red, clave rechazada...), lo deja en el
registro y lo vuelve a intentar en la siguiente ocasión.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..util import as_dict, as_int, as_str
from .aggregate import Stats
from .pack import PACK_FILE, encode_pack

log = logging.getLogger(__name__)

STATE_FILE = "publicado.json"
PUBLISH_EVERY_SECONDS = 3 * 3600
MIN_MATCHES = 100  # con menos partidas no merece la pena publicar
GIT_TIMEOUT = 120
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

README = """# Estadísticas de Erizo Coach

Builds, runas y hechizos más jugados por campeón y posición en partidas de alto elo,
agregados por Erizo Coach. Solo contiene datos agregados: ningún jugador ni partida concreta.

Último paquete: parche {patch}, {matches} partidas.

Erizo Coach no está respaldado por Riot Games y no refleja sus opiniones.
"""

Runner = Callable[..., subprocess.CompletedProcess[str]]


class PublishError(Exception):
    """No se han podido publicar las estadísticas."""


@dataclass(frozen=True, slots=True)
class PublishTarget:
    repo: str  # dirección SSH del repositorio de datos
    key: Path  # clave privada SSH (deploy key del repositorio de datos)


def from_settings(settings: Settings) -> PackPublisher | None:
    """Solo quien lo configura publica sus estadísticas para los demás."""
    if not settings.stats_publish_repo or settings.stats_publish_key is None:
        return None
    target = PublishTarget(settings.stats_publish_repo, settings.stats_publish_key)
    return PackPublisher(target, settings.stats_dir)


class PackPublisher:
    def __init__(
        self,
        target: PublishTarget,
        stats_dir: Path,
        *,
        runner: Runner = subprocess.run,
        clock: Callable[[], float] = time.time,
        every_seconds: float = PUBLISH_EVERY_SECONDS,
    ) -> None:
        self._target = target
        self._state = stats_dir / STATE_FILE
        self._run = runner
        self._now = clock
        self._every = every_seconds

    def maybe_publish(self, stats: Stats) -> bool:
        """Publica si toca. Bloqueante (se llama en un hilo). Devuelve si se ha publicado."""
        patch, matches, _ = stats
        if matches < MIN_MATCHES:
            return False
        last = self._last()
        if as_str(last.get("patch")) == patch and (
            as_int(last.get("matches")) == matches
            or self._now() - float(last.get("time") or 0) < self._every
        ):
            return False
        try:
            self.publish(stats)
        except PublishError as exc:
            log.warning("No se han podido publicar las estadísticas: %s", exc)
            return False
        return True

    def publish(self, stats: Stats) -> None:
        """Publica ya, sin esperar (lanza :class:`PublishError` si no se puede)."""
        patch, matches, _ = stats
        if matches < MIN_MATCHES:
            raise PublishError(f"hacen falta al menos {MIN_MATCHES} partidas (hay {matches})")
        try:
            self._push(stats)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PublishError(str(exc)) from exc
        self._remember({"patch": patch, "matches": matches, "time": int(self._now())})
        log.info("Estadísticas publicadas: parche %s, %d partidas", patch, matches)

    def _push(self, stats: Stats) -> None:
        git = shutil.which("git")
        if git is None:
            raise OSError("no se encuentra git")
        if not self._target.key.is_file():
            raise OSError(f"no existe la clave {self._target.key}")
        patch, matches, _ = stats
        with tempfile.TemporaryDirectory(prefix="lolcoach-publicar-") as tmp:
            work = Path(tmp)
            (work / PACK_FILE).write_bytes(encode_pack(stats))
            readme = README.format(patch=patch, matches=matches)
            (work / "README.md").write_bytes(readme.encode("utf-8"))
            message = f"Estadísticas del parche {patch} ({matches} partidas)"
            self._git(git, work, "init", "-q", "-b", "main")
            self._git(git, work, "add", PACK_FILE, "README.md")
            self._git(git, work, "commit", "-q", "-m", message)
            self._git(git, work, "push", "-q", "--force", self._target.repo, "HEAD:main")

    def _git(self, git: str, work: Path, *args: str) -> None:
        key = self._target.key.as_posix()
        env = {
            **os.environ,
            "GIT_SSH_COMMAND": f'ssh -i "{key}" -o IdentitiesOnly=yes -o BatchMode=yes',
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Erizo Coach",
            "GIT_AUTHOR_EMAIL": "lolcoach@localhost",
            "GIT_COMMITTER_NAME": "Erizo Coach",
            "GIT_COMMITTER_EMAIL": "lolcoach@localhost",
        }
        result = self._run(
            [git, "-c", "commit.gpgsign=false", *args],
            cwd=work, env=env, capture_output=True, text=True,
            timeout=GIT_TIMEOUT, check=False, creationflags=CREATE_NO_WINDOW,
        )  # fmt: skip
        if result.returncode != 0:
            detail = " ".join((result.stderr or result.stdout or "").split())[-300:]
            raise subprocess.SubprocessError(
                f"git {args[0]} ha fallado: {detail or result.returncode}"
            )

    def _last(self) -> dict[str, Any]:
        try:
            return dict(as_dict(json.loads(self._state.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            return {}

    def _remember(self, state: dict[str, Any]) -> None:
        try:
            self._state.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            log.warning("No se ha podido guardar el estado de la publicación: %s", exc)
