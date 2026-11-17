"""Clave pública Ed25519 con la que se comprueba el manifiesto de actualización.

La escribe `scripts/publicar_version.py --crear-clave`; la privada no está en el repo.
"""

from __future__ import annotations

PUBLIC_KEY: bytes = bytes.fromhex(
    "e31329c46a19f289c9948d422e8064eef7f623bc8d0c6e2bde51a6e7a6c2f934"
)
