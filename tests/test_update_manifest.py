from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from erizocoach.update.manifest import (
    Manifest,
    UpdateError,
    encode_manifest,
    parse_version,
    sign,
    verify_manifest,
)

SHA = "a" * 64


def _keys() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, public


def _manifest(**changes: object) -> Manifest:
    base = {
        "version": "1.2.3",
        "installer": "Instalar-Erizo-Coach-1.2.3.exe",
        "size": 1_000_000,
        "sha256": SHA,
        "notes": "Novedades",
        "published": "2026-11-17",
    }
    base.update(changes)
    return Manifest(**base)  # type: ignore[arg-type]


def test_signed_manifest_round_trips() -> None:
    private, public = _keys()
    m = _manifest()
    data = encode_manifest(m)
    assert verify_manifest(data, sign(data, private), public) == m


def test_tampered_manifest_is_rejected() -> None:
    private, public = _keys()
    data = encode_manifest(_manifest())
    signature = sign(data, private)
    tampered = bytearray(data)
    tampered[5] ^= 0x01
    with pytest.raises(UpdateError):
        verify_manifest(bytes(tampered), signature, public)


def test_other_key_is_rejected() -> None:
    private, _ = _keys()
    _, other_public = _keys()
    data = encode_manifest(_manifest())
    with pytest.raises(UpdateError):
        verify_manifest(data, sign(data, private), other_public)


def test_malformed_signature_and_key_are_rejected() -> None:
    private, public = _keys()
    data = encode_manifest(_manifest())
    with pytest.raises(UpdateError):
        verify_manifest(data, b"no es base64!!", public)
    with pytest.raises(UpdateError):
        verify_manifest(data, sign(data, private), b"corta")
    with pytest.raises(UpdateError):
        verify_manifest(data, sign(data, private), b"")


@pytest.mark.parametrize(
    "changes",
    [
        {"installer": "..\\malo.exe"},
        {"installer": "Instalar Erizo Coach 1.0.0.exe"},
        {"installer": "x.exe"},
        {"sha256": "abc"},
        {"sha256": "g" * 64},
        {"size": 0},
        {"size": -5},
        {"size": 200_000_001},
        {"version": "1.2"},
    ],
)
def test_bad_fields_are_rejected(changes: dict[str, object]) -> None:
    data = encode_manifest(_manifest(**changes))
    private, public = _keys()
    with pytest.raises(UpdateError):
        verify_manifest(data, sign(data, private), public)


@pytest.mark.parametrize("raw", [b"[1, 2]", b'"texto"', b"no es json"])
def test_non_object_json_is_rejected(raw: bytes) -> None:
    private, public = _keys()
    with pytest.raises(UpdateError):
        verify_manifest(raw, sign(raw, private), public)


def test_missing_field_is_rejected() -> None:
    private, public = _keys()
    raw = json.dumps({"version": "1.0.0"}).encode()
    with pytest.raises(UpdateError):
        verify_manifest(raw, sign(raw, private), public)


def test_versions_compare_numerically() -> None:
    assert parse_version("0.10.0") > parse_version("0.9.9")
    assert parse_version("1.2.3") == (1, 2, 3)
    with pytest.raises(UpdateError):
        parse_version("1.2")
