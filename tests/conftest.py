from __future__ import annotations

import ssl
from collections.abc import AsyncIterator, Iterator

import keyring
import keyring.errors
import pytest
import trustme
from keyring.backend import KeyringBackend

from erizocoach.lcu import launcher
from erizocoach.lcu.tls import create_ssl_context
from erizocoach.stats import pack
from erizocoach.stats.pack import PackError

from .fake_lcu import FakeLcu


class _EmptyKeyring(KeyringBackend):
    """Almacén de claves vacío: las pruebas nunca leen tu clave real de Riot."""

    priority = 1  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("almacén de pruebas")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.PasswordDeleteError("almacén de pruebas")


@pytest.fixture(autouse=True)
def no_real_keyring() -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(_EmptyKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def no_real_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Las pruebas nunca tocan tu carpeta de datos real (ni la mueven del nombre antiguo)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path_factory.mktemp("localappdata")))


@pytest.fixture(autouse=True)
def no_internet_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas nunca descargan el paquete de estadísticas real de internet."""

    async def offline(_url: str) -> bytes:
        raise PackError("sin internet en las pruebas")

    monkeypatch.setattr(pack, "download_pack", offline)


@pytest.fixture(autouse=True)
def no_real_riot_client(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Las pruebas nunca abren tu cliente de Riot ni hablan con su API local."""
    nowhere = tmp_path_factory.mktemp("sin-riot")
    monkeypatch.setattr(launcher, "default_riot_lockfile", lambda: nowhere / "lockfile")
    monkeypatch.setattr(launcher, "default_installs_file", lambda: nowhere / "installs.json")

    def refuse(exe: object) -> None:
        raise AssertionError(f"una prueba ha intentado abrir {exe}")

    monkeypatch.setattr(launcher, "spawn_detached", refuse)


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
