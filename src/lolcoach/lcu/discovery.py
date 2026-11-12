"""Detección del cliente de LoL y de sus credenciales temporales.

Cada vez que se abre, el cliente elige un puerto y una contraseña aleatorios y
los escribe en el archivo ``lockfile`` de su carpeta de instalación. Los
buscamos así:

1. En la carpeta ya conocida (rápido: leer un archivo pequeño).
2. Si no, buscando el proceso ``LeagueClientUx.exe``: su ruta nos da la
   carpeta (y la recordamos) y, en último caso, sus argumentos traen el puerto
   y la contraseña.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

CLIENT_PROCESS_NAME = "leagueclientux.exe"
LOCKFILE_NAME = "lockfile"


class LockfileError(ValueError):
    """El lockfile no tiene el formato esperado."""


@dataclass(frozen=True, slots=True)
class LcuCredentials:
    port: int
    password: str = field(repr=False)  # nunca aparece al imprimir el objeto
    pid: int | None = None
    username: str = "riot"

    @property
    def base_url(self) -> str:
        # Siempre HTTPS y siempre 127.0.0.1: nunca se envía nada fuera de tu PC.
        return f"https://127.0.0.1:{self.port}"

    @property
    def authorization(self) -> str:
        """Valor de la cabecera ``Authorization`` (``Basic <base64>``)."""
        return "Basic " + base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

    @property
    def secrets(self) -> tuple[str, ...]:
        """Textos que nunca deben aparecer en pantalla ni en los registros."""
        return (self.password, self.authorization.removeprefix("Basic "))


def parse_lockfile(text: str) -> LcuCredentials:
    """Formato: ``LeagueClient:<pid>:<puerto>:<contraseña>:https``."""
    parts = text.strip().split(":")
    if len(parts) != 5:
        raise LockfileError(f"se esperaban 5 campos y hay {len(parts)}")
    _name, pid, port, password, protocol = parts
    if protocol != "https":
        raise LockfileError(f"protocolo no admitido: {protocol!r}")
    return _build(port, password, pid)


def parse_command_line(args: Sequence[str]) -> LcuCredentials | None:
    """Lee ``--app-port=`` y ``--remoting-auth-token=`` de los argumentos del proceso."""
    port = token = None
    for arg in args:
        if arg.startswith("--app-port="):
            port = arg.partition("=")[2]
        elif arg.startswith("--remoting-auth-token="):
            token = arg.partition("=")[2]
    if port is None or token is None:
        return None
    try:
        return _build(port, token)
    except LockfileError:
        return None


def _build(port_raw: str, password: str, pid_raw: str | None = None) -> LcuCredentials:
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise LockfileError(f"puerto no válido: {port_raw!r}") from exc
    if not 0 < port < 65536:
        raise LockfileError(f"puerto fuera de rango: {port}")
    if not password:
        raise LockfileError("contraseña vacía")
    pid: int | None = None
    if pid_raw:
        try:
            pid = int(pid_raw)
        except ValueError as exc:
            raise LockfileError(f"PID no válido: {pid_raw!r}") from exc
    return LcuCredentials(port=port, password=password, pid=pid)


class ClientLocator:
    """Encuentra el cliente abierto. Sus métodos bloquean (disco/procesos): usar en un hilo."""

    def __init__(self, league_dir: Path | None = None) -> None:
        self._league_dir = league_dir

    @property
    def league_dir(self) -> Path | None:
        return self._league_dir

    def locate(self) -> LcuCredentials | None:
        if self._league_dir is not None:
            credentials = self._from_lockfile(self._league_dir / LOCKFILE_NAME)
            if credentials is not None:
                return credentials
        return self._from_process()

    def _from_lockfile(self, path: Path) -> LcuCredentials | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.debug("No se puede leer %s: %s", path, exc)
            return None
        try:
            credentials = parse_lockfile(text)
        except LockfileError as exc:
            log.debug("Lockfile no válido en %s: %s", path, exc)
            return None
        if credentials.pid is not None and not psutil.pid_exists(credentials.pid):
            log.debug("Lockfile antiguo: el proceso %s ya no existe", credentials.pid)
            return None
        return credentials

    def _from_process(self) -> LcuCredentials | None:
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name") or ""
            if name.lower() != CLIENT_PROCESS_NAME:
                continue
            exe = _safe_call(proc.exe)
            if exe:
                league_dir = Path(exe).parent
                credentials = self._from_lockfile(league_dir / LOCKFILE_NAME)
                if credentials is not None:
                    if league_dir != self._league_dir:
                        log.info("Cliente de LoL encontrado en %s", league_dir)
                        self._league_dir = league_dir
                    return credentials
            credentials = parse_command_line(_safe_call(proc.cmdline) or [])
            if credentials is not None:
                return credentials
        return None


def _safe_call[T](func: Callable[[], T]) -> T | None:
    try:
        return func()
    except (psutil.Error, OSError) as exc:
        log.debug("No se puede inspeccionar el proceso del cliente: %s", exc)
        return None
