"""Conexión cifrada y verificada con el cliente.

Muchas herramientas desactivan la verificación TLS para hablar con el cliente.
Aquí NO: solo se confía en la autoridad de certificación oficial de Riot
(``certs/riotgames.pem``, publicada en developer.riotgames.com) y se comprueba
que el certificado es para 127.0.0.1. Si otro programa intentara hacerse pasar
por el cliente, la conexión se rechaza.
"""

from __future__ import annotations

import ssl
from importlib.resources import files

RIOT_CA_RESOURCE = "certs/riotgames.pem"


def load_riot_ca() -> str:
    return files("erizocoach.lcu").joinpath(RIOT_CA_RESOURCE).read_text(encoding="ascii")


def create_ssl_context(ca_pem: str | None = None) -> ssl.SSLContext:
    """Contexto TLS que confía únicamente en la CA de Riot (o en ``ca_pem``, para tests)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # exige certificado válido y nombre correcto
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # La CA de Riot (2013) no incluye la extensión "Authority Key Identifier", que el
    # modo estricto de Python 3.13 exige. Se desactiva solo esa comprobación extra:
    # la cadena de confianza y el nombre del host se siguen verificando.
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ctx.load_verify_locations(cadata=ca_pem if ca_pem is not None else load_riot_ca())
    return ctx
