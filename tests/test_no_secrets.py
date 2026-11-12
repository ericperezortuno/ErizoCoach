"""Salvaguarda: la contraseña del cliente real nunca debe acabar en el repositorio.

Si el cliente de LoL está abierto, comprueba que su contraseña temporal (ni en
claro ni codificada) no aparece en ningún archivo del proyecto. Si el cliente
está cerrado, la prueba se omite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.lcu.discovery import ClientLocator

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("src", "tests", "README.md", "pyproject.toml")
TEXT_SUFFIXES = {".py", ".json", ".md", ".toml", ".txt", ".bat", ".pem"}


def _project_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCANNED:
        path = ROOT / entry
        candidates = [path] if path.is_file() else path.rglob("*")
        files.extend(p for p in candidates if p.is_file() and p.suffix in TEXT_SUFFIXES)
    return files


def test_live_client_password_is_not_in_the_project() -> None:
    credentials = ClientLocator().locate()
    if credentials is None:
        pytest.skip("el cliente de LoL no está abierto")
    leaks = [
        str(path.relative_to(ROOT))
        for path in _project_files()
        if any(secret in path.read_text(encoding="utf-8", errors="ignore")
               for secret in credentials.secrets)
    ]  # fmt: skip
    assert leaks == [], f"La contraseña del cliente aparece en: {leaks}"
