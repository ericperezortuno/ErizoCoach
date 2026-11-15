"""Abre el cliente de League of Legends desde la ventana (botón «Abrir League of Legends»).

Dos pasos:

1. Abre el **cliente de Riot** como su acceso directo oficial
   (``--launch-product=league_of_legends --launch-patchline=live``). Si estaba cerrado
   arranca; si estaba en segundo plano o detrás, se pone delante (y pide iniciar sesión si
   hace falta).
2. Ese acceso directo **no basta** cuando el cliente de Riot ya estaba abierto: decide si
   abre el LoL antes de terminar de iniciar tu sesión y lo cancela (en su registro:
   ``willAutoLaunch=false`` por ``game:play ... UNAUTHORIZED``). Así que, en cuanto el
   cliente de Riot tiene tu sesión iniciada, se le pide que abra el LoL por su API local
   (``POST /product-launcher/v1/products/league_of_legends/patchlines/live``: lo mismo que
   su botón «Jugar»), salvo que ya lo esté abriendo él.

Seguridad:

* La ruta del cliente de Riot sale del archivo que escribe el propio instalador de Riot
  (``%PROGRAMDATA%\\Riot Games\\RiotClientInstalls.json``). Solo se acepta un
  ``RiotClientServices.exe`` que exista, con ruta absoluta.
* Se lanza sin consola y sin intérprete de comandos (lista de argumentos fijos), y queda
  independiente: si cierras Erizo Coach, el cliente sigue abierto.
* La API del cliente de Riot es local (127.0.0.1), con la contraseña de su ``lockfile``
  (que nunca sale en los registros) y con el certificado verificado con la CA de Riot,
  igual que con el cliente de LoL.
* Un clic repetido mientras se está abriendo no lanza otra copia.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..bus import EventBus
from ..events import (
    ClientConnected,
    ClientDisconnected,
    ClientLaunchStatus,
    LaunchClientRequested,
    LaunchState,
)
from ..logs import REDACTOR
from ..util import as_dict
from .client import LcuClient, LcuError, LcuUnavailable
from .discovery import LcuCredentials, LockfileError, parse_lockfile
from .tls import create_ssl_context

log = logging.getLogger(__name__)

RIOT_CLIENT_EXE = "riotclientservices.exe"
LAUNCH_ARGS = ("--launch-product=league_of_legends", "--launch-patchline=live")
MAX_INSTALLS_FILE = 256 * 1024  # el archivo real ocupa menos de 1 KB
# Lo que puede tardar en abrirse el cliente de LoL (iniciar sesión, actualizar…): mientras
# tanto el botón queda en «Abriendo…» y otro clic no lanza nada.
STARTING_TIMEOUT = 180.0
POLL_INTERVAL = 2.0  # cada cuánto se mira si el cliente de Riot ya tiene la sesión iniciada
LEAGUE_PRODUCT = "league_of_legends"
LAUNCH_PATH = "/product-launcher/v1/products/league_of_legends/patchlines/live"
SESSIONS_PATH = "/product-session/v1/sessions"
AUTHORIZATION_PATH = "/rso-auth/v1/authorization"  # 404 = sin iniciar sesión
MAX_LOCKFILE = 4096

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def default_riot_lockfile() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Riot Games" / "Riot Client" / "Config" / "lockfile"


def read_riot_lockfile(path: Path) -> LcuCredentials | None:
    """Puerto y contraseña de la API local del cliente de Riot (None si no está abierto)."""
    try:
        if path.stat().st_size > MAX_LOCKFILE:
            return None
        credentials = parse_lockfile(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, LockfileError):
        return None
    REDACTOR.add_secret(*credentials.secrets)
    return credentials


def default_installs_file() -> Path:
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(program_data) / "Riot Games" / "RiotClientInstalls.json"


def find_riot_client(installs_file: Path, league_dir: Path | None = None) -> Path | None:
    """Ruta de ``RiotClientServices.exe`` según el instalador de Riot, o None."""
    try:
        if installs_file.stat().st_size > MAX_INSTALLS_FILE:
            log.warning("%s es demasiado grande; no se usa", installs_file)
            return None
        data = as_dict(json.loads(installs_file.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        log.debug("No se pudo leer %s: %s", installs_file, exc)
        return None
    # Primero el cliente asociado a tu carpeta de LoL; después el de Riot por defecto.
    associated = as_dict(data.get("associated_client"))
    wanted = _folder_key(league_dir) if league_dir else None
    exact = [c for d, c in associated.items() if wanted and _folder_key(d) == wanted]
    league = [c for d, c in associated.items() if "league of legends" in _folder_key(d)]
    candidates: list[Any] = [*exact, *league, data.get("rc_live"), data.get("rc_default")]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        path = Path(candidate)
        if path.is_absolute() and path.name.casefold() == RIOT_CLIENT_EXE and path.is_file():
            return path
    return None


def _folder_key(folder: object) -> str:
    return str(folder).replace("\\", "/").casefold().rstrip("/")


def spawn_detached(exe: Path) -> None:
    """Lanza el cliente de Riot independiente de Erizo Coach (sin consola ni shell)."""
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    subprocess.Popen(  # ruta validada y argumentos fijos
        [str(exe), *LAUNCH_ARGS],
        cwd=str(exe.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags if sys.platform == "win32" else 0,
    )


class ClientLauncher:
    def __init__(
        self,
        bus: EventBus,
        *,
        installs_file: Path | None = None,
        league_dir: Path | None = None,
        spawn: Callable[[Path], None] | None = None,
        riot_lockfile: Path | None = None,
        ssl_context: ssl.SSLContext | None = None,
        starting_timeout: float = STARTING_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self._bus = bus
        self._installs_file = installs_file or default_installs_file()
        self._league_dir = league_dir
        self._spawn = spawn or spawn_detached
        self._riot_lockfile = riot_lockfile or default_riot_lockfile()
        self._ssl_context = ssl_context or create_ssl_context()
        self._starting_timeout = starting_timeout
        self._poll_interval = poll_interval
        self._connected = False
        self._waiting: asyncio.Task[None] | None = None  # «Abriendo…» hasta que conecte
        bus.subscribe(LaunchClientRequested, self._on_request)
        bus.subscribe(ClientConnected, self._on_connected)
        bus.subscribe(ClientDisconnected, self._on_disconnected)

    @property
    def starting(self) -> bool:
        return self._waiting is not None and not self._waiting.done()

    async def _on_connected(self, _event: ClientConnected) -> None:
        self._connected = True
        self._stop_waiting()

    async def _on_disconnected(self, _event: ClientDisconnected) -> None:
        self._connected = False

    async def _on_request(self, _event: LaunchClientRequested) -> None:
        if self._connected or self.starting:
            return  # ya está abierto, o abriéndose
        exe = await asyncio.to_thread(find_riot_client, self._installs_file, self._league_dir)
        if exe is None:
            log.warning("No se encuentra el cliente de Riot (%s)", self._installs_file)
            await self._bus.publish(ClientLaunchStatus(LaunchState.NOT_FOUND))
            return
        try:
            await asyncio.to_thread(self._spawn, exe)
        except OSError as exc:
            log.warning("No se pudo abrir el cliente de Riot: %s", exc)
            await self._bus.publish(ClientLaunchStatus(LaunchState.FAILED))
            return
        log.info("Abriendo el cliente de Riot para League of Legends (%s)", exe)
        await self._bus.publish(ClientLaunchStatus(LaunchState.STARTING))
        self._waiting = asyncio.create_task(self._finish_launch(), name="lanzar-cliente")

    async def _finish_launch(self) -> None:
        """Hasta que conecte el cliente de LoL (o se agote el tiempo): en cuanto el cliente
        de Riot tenga tu sesión iniciada, se le pide que abra el LoL (una sola vez)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._starting_timeout
        asked = False
        while (left := deadline - loop.time()) > 0:
            await asyncio.sleep(min(self._poll_interval, left))
            if not asked:
                asked = await self._ask_riot_client()
        # No ha llegado a conectar: el botón vuelve a estar disponible.
        await self._bus.publish(ClientLaunchStatus(LaunchState.IDLE))

    async def _ask_riot_client(self) -> bool:
        """True si el LoL ya se está abriendo (lo ha abierto él o se lo hemos pedido)."""
        credentials = await asyncio.to_thread(read_riot_lockfile, self._riot_lockfile)
        if credentials is None:
            return False  # el cliente de Riot aún no ha arrancado
        try:
            async with LcuClient(credentials, self._ssl_context) as riot:
                sessions = as_dict(await riot.get(SESSIONS_PATH))
                if any(as_dict(s).get("productId") == LEAGUE_PRODUCT for s in sessions.values()):
                    log.info("El cliente de Riot ya está abriendo League of Legends")
                    return True
                await riot.get(AUTHORIZATION_PATH)  # sin sesión iniciada: error y se reintenta
                try:
                    await riot.post(LAUNCH_PATH)
                except LcuUnavailable as exc:
                    # El cliente de Riot no contesta hasta haber lanzado el LoL (tarda más
                    # que nuestro límite de espera), pero la petición ya le ha llegado: no
                    # se repite, para no abrir dos.
                    log.info("Pedido al cliente de Riot que abra League of Legends (%s)", exc)
                    return True
        except LcuError as exc:
            # En segundo plano, sin sesión iniciada o arrancando: aún no se puede.
            log.debug("El cliente de Riot aún no puede abrir el LoL: %s", exc)
            return False
        log.info("Pedido al cliente de Riot que abra League of Legends")
        return True

    def _stop_waiting(self) -> None:
        if self._waiting is not None and not self._waiting.done():
            self._waiting.cancel()
        self._waiting = None
