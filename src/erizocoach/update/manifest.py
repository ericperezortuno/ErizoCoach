"""Manifiesto de versión firmado con Ed25519.

La firma se comprueba antes de leer ningún campo del JSON: lo que no está
firmado por nosotros no se interpreta.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MAX_INSTALLER_BYTES = 200_000_000
INSTALLER_NAME = re.compile(r"^Instalar-Erizo-Coach-\d{1,3}\.\d{1,3}\.\d{1,3}\.exe$")
_VERSION = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(Exception):
    """Fallo al comprobar o aplicar una actualización."""


@dataclass(frozen=True, slots=True)
class Manifest:
    version: str
    installer: str
    size: int
    sha256: str
    notes: str
    published: str


def parse_version(text: str) -> tuple[int, int, int]:
    """Convierte «X.Y.Z» en una tupla comparable numéricamente."""
    match = _VERSION.match(text) if isinstance(text, str) else None
    if match is None:
        raise UpdateError(f"Versión no válida: {text!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def encode_manifest(m: Manifest) -> bytes:
    """Serializa el manifiesto como JSON UTF-8 con claves ordenadas."""
    fields = {
        "version": m.version,
        "installer": m.installer,
        "size": m.size,
        "sha256": m.sha256,
        "notes": m.notes,
        "published": m.published,
    }
    return json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign(data: bytes, private_key: bytes) -> bytes:
    """Firma `data` y devuelve la firma en base64 ASCII (la usa el script de publicación)."""
    try:
        key = Ed25519PrivateKey.from_private_bytes(private_key)
    except ValueError as exc:
        raise UpdateError("Clave privada no válida") from exc
    return base64.b64encode(key.sign(data))


def verify_manifest(data: bytes, signature: bytes, public_key: bytes) -> Manifest:
    """Comprueba la firma y, solo entonces, lee y valida los campos."""
    try:
        key = Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError as exc:
        raise UpdateError("Clave pública no válida") from exc
    try:
        raw_signature = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateError("Firma mal formada") from exc
    try:
        key.verify(raw_signature, data)
    except InvalidSignature as exc:
        raise UpdateError("La firma del manifiesto no es válida") from exc
    return _parse_manifest(data)


def _parse_manifest(data: bytes) -> Manifest:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("Manifiesto ilegible") from exc
    if not isinstance(raw, dict):
        raise UpdateError("El manifiesto no es un objeto JSON")
    try:
        manifest = Manifest(
            version=raw["version"],
            installer=raw["installer"],
            size=raw["size"],
            sha256=raw["sha256"],
            notes=raw["notes"],
            published=raw["published"],
        )
    except KeyError as exc:
        raise UpdateError(f"Falta el campo {exc.args[0]!r} en el manifiesto") from exc
    parse_version(manifest.version)
    if not isinstance(manifest.installer, str) or not INSTALLER_NAME.match(manifest.installer):
        raise UpdateError("Nombre de instalador no válido")
    if (
        not isinstance(manifest.size, int)
        or isinstance(manifest.size, bool)
        or not 0 < manifest.size <= MAX_INSTALLER_BYTES
    ):
        raise UpdateError("Tamaño de instalador no válido")
    if not isinstance(manifest.sha256, str) or not _SHA256.match(manifest.sha256):
        raise UpdateError("Huella sha256 no válida")
    if not isinstance(manifest.notes, str) or not isinstance(manifest.published, str):
        raise UpdateError("Notas o fecha no válidas")
    return manifest
