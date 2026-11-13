"""Guarda tu clave de la API de Riot de forma segura.

* Se guarda cifrada en el **Administrador de credenciales de Windows** (el mismo
  sitio donde Windows guarda sus contraseñas), no en un archivo de texto.
* Nunca se muestra, ni se escribe en los registros (se añade al filtro de secretos).
* Solo se envía a los servidores oficiales de la API de Riot, por HTTPS y en una
  cabecera (nunca en la dirección).
"""

from __future__ import annotations

import logging
import re

import keyring
from keyring.errors import KeyringError

from ..logs import REDACTOR

log = logging.getLogger(__name__)

SERVICE = "LoLCoach"
ACCOUNT = "riot_api_key"
KEY_PATTERN = re.compile(
    r"^RGAPI-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class KeyStoreError(Exception):
    """No se pudo leer o guardar la clave."""


def is_valid_format(key: str) -> bool:
    return bool(KEY_PATTERN.match(key.strip()))


def save_key(key: str) -> None:
    key = key.strip()
    if not is_valid_format(key):
        raise KeyStoreError(
            "la clave no tiene el formato RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        )
    try:
        keyring.set_password(SERVICE, ACCOUNT, key)
    except KeyringError as exc:
        raise KeyStoreError(
            f"no se pudo guardar en el Administrador de credenciales: {exc}"
        ) from exc
    REDACTOR.add_secret(key)


def load_key() -> str | None:
    try:
        key = keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError as exc:
        log.warning("No se pudo leer la clave de Riot: %s", exc)
        return None
    if key:
        REDACTOR.add_secret(key)
    return key or None


def delete_key() -> bool:
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        return False
    return True
