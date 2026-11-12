from __future__ import annotations

import ssl
from collections.abc import AsyncIterator

import pytest
import trustme

from lolcoach.lcu.tls import create_ssl_context

from .fake_lcu import FakeLcu


@pytest.fixture
def ca() -> trustme.CA:
    """Autoridad de certificación de prueba (hace el papel de la CA de Riot)."""
    return trustme.CA()


@pytest.fixture
def client_ssl(ca: trustme.CA) -> ssl.SSLContext:
    return create_ssl_context(ca.cert_pem.bytes().decode("ascii"))


@pytest.fixture
async def fake(ca: trustme.CA) -> AsyncIterator[FakeLcu]:
    server_ssl = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert("127.0.0.1", "localhost").configure_cert(server_ssl)
    server = FakeLcu()
    await server.start(server_ssl)
    yield server
    await server.stop()
