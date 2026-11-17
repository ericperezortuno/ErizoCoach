"""Publica una versión de Erizo Coach en GitHub (solo para quien mantiene el proyecto).

Uso:
    uv run python scripts/publicar_version.py --crear-clave   (una vez) genera el par de claves
    uv run python scripts/publicar_version.py --token         (una vez) guarda el token de GitHub
    uv run python scripts/publicar_version.py                 publica la versión de ``__version__``

Antes hay que crear la etiqueta ``vX.Y.Z`` (con el mensaje que serán las notas) y subirla a
``origin``: este script no hace ``git push`` ni crea etiquetas. Construye el instalador, escribe y
firma el manifiesto, crea la release y sube los tres archivos. El token de GitHub y la clave
privada de firma viven solo en el Administrador de credenciales de Windows (servicio
``ErizoCoach-publicar``): nunca en el repositorio, en el ``.exe`` ni en los registros.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import keyring
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SIGNING_KEY_FILE = ROOT / "src" / "erizocoach" / "update" / "signing_key.py"

sys.path.insert(0, str(ROOT / "src"))
from erizocoach import __version__  # noqa: E402
from erizocoach.logs import REDACTOR  # noqa: E402
from erizocoach.update import signing_key  # noqa: E402
from erizocoach.update.download import MANIFEST_URL, SIGNATURE_URL, Downloader  # noqa: E402
from erizocoach.update.manifest import (  # noqa: E402
    Manifest,
    UpdateError,
    encode_manifest,
    sign,
    verify_manifest,
)

log = logging.getLogger(__name__)

SERVICE = "ErizoCoach-publicar"
TOKEN_ACCOUNT = "github_token"
KEY_ACCOUNT = "clave_firma"
REPO = "ericperezortuno/ErizoCoach"
MANIFEST_NAME = "erizocoach-version.json"
SIGNATURE_NAME = MANIFEST_NAME + ".sig"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
HTTP_TIMEOUT = 600

RunGit = Callable[[list[str]], tuple[int, str]]


class PublishError(Exception):
    """Algo impide publicar; el mensaje (en español) se muestra tal cual."""


# --- Git ---------------------------------------------------------------------------------------


def run_git(args: list[str]) -> tuple[int, str]:
    """Ejecuta git en el repositorio. Devuelve (código de salida, salida estándar)."""
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, encoding="utf-8"
    )
    return result.returncode, result.stdout


def preflight(version: str, run_git: RunGit = run_git) -> list[str]:
    """Problemas que impiden publicar: cambios sin guardar o etiqueta ausente / sin subir."""
    tag = f"v{version}"
    problems: list[str] = []
    code, out = run_git(["status", "--porcelain"])
    if code != 0:
        problems.append("No se ha podido consultar el estado de git.")
    elif out.strip():
        problems.append("Hay cambios sin guardar (commit) en el repositorio.")
    code, tagged = run_git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"])
    if code != 0:
        problems.append(f"No existe la etiqueta {tag} (créala con «git tag -a {tag} -m ...»).")
    else:
        head_code, head = run_git(["rev-parse", "HEAD"])
        if head_code != 0 or head.strip() != tagged.strip():
            problems.append(
                f"La etiqueta {tag} no apunta al commit actual: "
                "publica desde ese commit o vuelve a etiquetar."
            )
    code, out = run_git(["ls-remote", "--tags", "origin", f"refs/tags/{tag}"])
    if code != 0 or not out.strip():
        problems.append(f"La etiqueta {tag} no está en origin («git push origin {tag}»).")
    return problems


def release_notes(tag: str, run_git: RunGit = run_git) -> str:
    """Notas de la versión: el asunto del mensaje de la etiqueta."""
    code, out = run_git(["tag", "-l", "--format=%(contents:subject)", tag])
    if code != 0:
        raise PublishError(f"No se han podido leer las notas de la etiqueta {tag}.")
    return out.strip()


# --- Claves y token ----------------------------------------------------------------------------


def create_signing_key(key_file: Path = SIGNING_KEY_FILE, store: Any = keyring) -> str:
    """Genera el par Ed25519: privada al keyring, pública (hex) en ``key_file``. Devuelve la hex."""
    if store.get_password(SERVICE, KEY_ACCOUNT):
        raise PublishError("Ya existe una clave de firma en el Administrador de credenciales.")
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_hex = private.public_key().public_bytes_raw().hex()
    store.set_password(SERVICE, KEY_ACCOUNT, base64.b64encode(raw_private).decode("ascii"))
    key_file.write_text(
        '"""Clave pública Ed25519 con la que se comprueba el manifiesto de actualización.\n\n'
        "La escribe `scripts/publicar_version.py --crear-clave`; la privada no está en el repo.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        f'PUBLIC_KEY: bytes = bytes.fromhex("{public_hex}")\n',
        encoding="utf-8",
    )
    return public_hex


def store_token(store: Any = keyring, ask: Callable[[str], str] = getpass.getpass) -> None:
    """Pide el token de GitHub sin mostrarlo y lo guarda en el keyring."""
    token = ask("Token de GitHub (no se muestra): ").strip()
    if not token:
        raise PublishError("El token está vacío; no se ha guardado nada.")
    store.set_password(SERVICE, TOKEN_ACCOUNT, token)


def read_public_key(key_file: Path) -> bytes:
    """Lee la clave pública hex de un ``signing_key.py`` (para pruebas y comprobaciones)."""
    text = key_file.read_text(encoding="utf-8")
    start = text.find('bytes.fromhex("')
    if start < 0:
        return b""
    start += len('bytes.fromhex("')
    return bytes.fromhex(text[start : text.index('"', start)])


def _private_key(store: Any) -> bytes:
    encoded = store.get_password(SERVICE, KEY_ACCOUNT)
    if not encoded:
        raise PublishError("No hay clave de firma: ejecuta primero --crear-clave.")
    try:
        return base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise PublishError("La clave de firma guardada está dañada.") from exc


# --- Manifiesto --------------------------------------------------------------------------------


def build_manifest(installer: Path, version: str, notes: str, published: str) -> Manifest:
    """Describe el instalador: nombre, tamaño y SHA-256 reales."""
    digest = hashlib.sha256()
    with installer.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return Manifest(
        version=version,
        installer=installer.name,
        size=installer.stat().st_size,
        sha256=digest.hexdigest(),
        notes=notes,
        published=published,
    )


# --- GitHub ------------------------------------------------------------------------------------


class GitHubReleases:
    """Cliente mínimo de la API REST de GitHub. El token solo viaja en la cabecera."""

    def __init__(self, token: str, repo: str = REPO, opener: Any = urllib.request.urlopen) -> None:
        self._token = token
        self._repo = repo
        self._opener = opener
        REDACTOR.add_secret(token)

    def _request(
        self, url: str, method: str, data: bytes | None = None, content_type: str | None = None
    ) -> Any:
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "ErizoCoach-publicar")
        if content_type:
            request.add_header("Content-Type", content_type)
        return self._opener(request, timeout=HTTP_TIMEOUT)

    def release_exists(self, tag: str) -> bool:
        url = f"{API}/repos/{self._repo}/releases/tags/{urllib.parse.quote(tag, safe='')}"
        try:
            with self._request(url, "GET") as response:
                return response.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise PublishError(f"GitHub ha respondido {exc.code} al buscar la release.") from exc
        except urllib.error.URLError as exc:
            raise PublishError("No se ha podido conectar con GitHub.") from exc

    def create_release(self, tag: str, name: str, body: str) -> int:
        payload = json.dumps({"tag_name": tag, "name": name, "body": body}).encode("utf-8")
        try:
            with self._request(
                f"{API}/repos/{self._repo}/releases", "POST", payload, "application/json"
            ) as response:
                return int(json.loads(response.read())["id"])
        except urllib.error.HTTPError as exc:
            raise PublishError(f"GitHub ha rechazado crear la release ({exc.code}).") from exc
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            raise PublishError("No se ha podido crear la release en GitHub.") from exc

    def upload_asset(self, release_id: int, path: Path, name: str, content_type: str) -> None:
        quoted = urllib.parse.quote(name, safe="")
        url = f"{UPLOADS}/repos/{self._repo}/releases/{release_id}/assets?name={quoted}"
        try:
            with self._request(url, "POST", path.read_bytes(), content_type):
                pass
        except urllib.error.HTTPError as exc:
            raise PublishError(f"GitHub ha rechazado subir {name} ({exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise PublishError(f"No se ha podido subir {name} a GitHub.") from exc


# --- Comprobación final ------------------------------------------------------------------------


def _download_published() -> tuple[bytes, bytes]:
    async def fetch() -> tuple[bytes | None, bytes | None]:
        downloader = Downloader()
        return (
            await downloader.fetch(MANIFEST_URL, 100_000),
            await downloader.fetch(SIGNATURE_URL, 10_000),
        )

    manifest, signature = asyncio.run(fetch())
    if manifest is None or signature is None:
        raise UpdateError("GitHub todavía no sirve la última versión")
    return manifest, signature


def verify_published(
    version: str,
    public_key: bytes,
    fetch: Callable[[], tuple[bytes, bytes]] = _download_published,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 6,
    delay: float = 5.0,
) -> None:
    """Descarga lo publicado y comprueba firma y versión (GitHub puede tardar unos segundos)."""
    last = "sin intentos"
    for attempt in range(attempts):
        if attempt:
            sleep(delay)
        try:
            manifest_bytes, signature = fetch()
            manifest = verify_manifest(manifest_bytes, signature, public_key)
        except UpdateError as exc:
            last = str(exc)
            continue
        if manifest.version == version:
            return
        last = f"GitHub sirve la versión {manifest.version}"
    raise PublishError(f"La comprobación final ha fallado: {last}.")


# --- Publicación -------------------------------------------------------------------------------


def run_build() -> None:
    """Construye la app y después el instalador (el orden importa)."""
    for script in ("build_exe.py", "build_installer.py"):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, check=False
        )
        if result.returncode != 0:
            raise PublishError(f"Ha fallado {script}.")


def publish(
    version: str,
    *,
    store: Any = keyring,
    run_git: RunGit = run_git,
    run_build: Callable[[], None] = run_build,
    github_factory: Callable[[str], GitHubReleases] = GitHubReleases,
    public_key: bytes | None = None,
    dist: Path = DIST,
    check_published: Callable[[str, bytes], None] = verify_published,
) -> int:
    """Publica ``version``. Devuelve el código de salida; los fallos se cuentan en español."""
    try:
        _publish(
            version, store, run_git, run_build, github_factory, public_key, dist, check_published
        )
    except PublishError as exc:
        print(exc)
        return 1
    return 0


def _publish(
    version: str,
    store: Any,
    git: RunGit,
    build: Callable[[], None],
    github_factory: Callable[[str], GitHubReleases],
    public_key: bytes | None,
    dist: Path,
    check_published: Callable[[str, bytes], None],
) -> None:
    public = signing_key.PUBLIC_KEY if public_key is None else public_key
    if not public:
        raise PublishError("No hay clave pública en el proyecto: ejecuta primero --crear-clave.")
    private = _private_key(store)
    try:
        derived = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw()
    except ValueError as exc:
        raise PublishError("La clave de firma guardada está dañada.") from exc
    if derived != public:
        raise PublishError("La clave privada guardada no corresponde a la clave pública del repo.")
    token = store.get_password(SERVICE, TOKEN_ACCOUNT)
    if not token:
        raise PublishError("No hay token de GitHub: ejecuta primero --token.")
    REDACTOR.add_secret(token, base64.b64encode(private).decode("ascii"))

    problems = preflight(version, run_git=git)
    if problems:
        raise PublishError("No se puede publicar todavía:\n- " + "\n- ".join(problems))

    tag = f"v{version}"
    github = github_factory(token)
    if github.release_exists(tag):
        raise PublishError(f"La release {tag} ya existe en GitHub: no se ha cambiado nada.")
    notes = release_notes(tag, run_git=git)

    print("Construyendo la app y el instalador...")
    build()
    source = dist / f"Instalar Erizo Coach {version}.exe"
    if not source.exists():
        raise PublishError(f"No se ha encontrado {source.name} tras construir.")
    installer = dist / f"Instalar-Erizo-Coach-{version}.exe"
    shutil.copyfile(source, installer)
    published = datetime.now(UTC).isoformat(timespec="seconds")
    manifest = encode_manifest(build_manifest(installer, version, notes, published))
    signature = sign(manifest, private)
    manifest_file = dist / MANIFEST_NAME
    signature_file = dist / SIGNATURE_NAME
    manifest_file.write_bytes(manifest)
    signature_file.write_bytes(signature)

    print(f"Creando la release {tag}...")
    release_id = github.create_release(tag, f"Erizo Coach {version}", notes)
    uploads = (
        (manifest_file, MANIFEST_NAME, "application/json"),
        (signature_file, SIGNATURE_NAME, "text/plain"),
        (installer, installer.name, "application/vnd.microsoft.portable-executable"),
    )
    for path, name, content_type in uploads:
        print(f"Subiendo {name}...")
        github.upload_asset(release_id, path, name, content_type)

    print("Comprobando lo publicado...")
    check_published(version, public)
    print(f"Publicada la versión {version}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publica una versión firmada de Erizo Coach.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--crear-clave", action="store_true", help="genera el par de claves")
    group.add_argument("--token", action="store_true", help="guarda el token de GitHub")
    args = parser.parse_args(argv)
    try:
        if args.crear_clave:
            public_hex = create_signing_key()
            print(f"Clave creada. Clave pública: {public_hex}")
            print("Haz commit de src/erizocoach/update/signing_key.py antes de publicar.")
            return 0
        if args.token:
            store_token()
            print("Token guardado en el Administrador de credenciales.")
            return 0
    except (PublishError, keyring.errors.KeyringError) as exc:
        print(exc)
        return 1
    return publish(__version__)


if __name__ == "__main__":
    sys.exit(main())
