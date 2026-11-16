"""Clave pública Ed25519 con la que se comprueba el manifiesto de actualización.

La clave real (32 bytes) la escribe `scripts/publicar_version.py --crear-clave`.
Mientras esté vacía, la comprobación de actualizaciones queda desactivada.
"""

from __future__ import annotations

PUBLIC_KEY: bytes = b""
