from __future__ import annotations

import os
from pathlib import Path

import pytest

from lolcoach.lcu.discovery import (
    ClientLocator,
    LcuCredentials,
    LockfileError,
    parse_command_line,
    parse_lockfile,
)


def test_parse_lockfile() -> None:
    # Valores inventados (nunca uses aquí datos de tu cliente real)
    creds = parse_lockfile("LeagueClient:1234:54321:contrasena-de-prueba-01:https\n")
    assert creds.port == 54321 and creds.pid == 1234
    assert creds.password == "contrasena-de-prueba-01"
    assert creds.base_url == "https://127.0.0.1:54321"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "LeagueClient:1:2:3",
        "LeagueClient:1:abc:pwd:https",
        "LeagueClient:1:70000:pwd:https",
        "LeagueClient:1:5000::https",
        "LeagueClient:1:5000:pwd:http",  # nunca sin cifrar
        "LeagueClient:x:5000:pwd:https",
    ],
)
def test_parse_lockfile_rejects_invalid(text: str) -> None:
    with pytest.raises(LockfileError):
        parse_lockfile(text)


def test_password_never_in_repr_and_secrets_cover_header() -> None:
    creds = LcuCredentials(port=1234, password="s3cr3t-token", pid=1)
    assert "s3cr3t-token" not in repr(creds)
    password, encoded = creds.secrets
    assert password == "s3cr3t-token"
    assert creds.authorization == f"Basic {encoded}"


def test_network_errors_never_expose_authorization_header() -> None:
    """Regresión: repr() de los errores de aiohttp incluye las cabeceras de la petición."""
    import aiohttp
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    from lolcoach.lcu.client import describe_error

    creds = LcuCredentials(port=1234, password="s3cr3t-token")
    url = URL("https://127.0.0.1:1234/")
    headers = CIMultiDictProxy(CIMultiDict({"Authorization": creds.authorization}))
    error = aiohttp.WSServerHandshakeError(
        aiohttp.RequestInfo(url, "GET", headers, url), (), status=404, message="Invalid"
    )
    assert creds.secrets[1] in repr(error)  # por eso nunca usamos repr()
    assert all(secret not in describe_error(error) for secret in creds.secrets)


def test_parse_command_line() -> None:
    args = [
        "LeagueClientUx.exe",
        "--riotclient-app-port=1",
        "--app-port=50418",
        "--remoting-auth-token=tok",
        "--locale=es_ES",
    ]
    creds = parse_command_line(args)
    assert creds is not None and creds.port == 50418 and creds.password == "tok"
    assert parse_command_line(["--app-port=50418"]) is None


def test_locator_reads_lockfile_of_running_process(tmp_path: Path) -> None:
    (tmp_path / "lockfile").write_text(f"LeagueClient:{os.getpid()}:5555:pwd:https")
    creds = ClientLocator(tmp_path).locate()
    assert creds is not None and creds.port == 5555


def test_locator_ignores_stale_lockfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "lockfile").write_text("LeagueClient:999999:5555:pwd:https")
    monkeypatch.setattr("lolcoach.lcu.discovery.psutil.pid_exists", lambda pid: False)
    monkeypatch.setattr("lolcoach.lcu.discovery.psutil.process_iter", lambda attrs: iter(()))
    assert ClientLocator(tmp_path).locate() is None


def test_locator_finds_process_and_remembers_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "lockfile").write_text(f"LeagueClient:{os.getpid()}:6000:pwd:https")

    class FakeProcess:
        info = {"name": "LeagueClientUx.exe"}

        def exe(self) -> str:
            return str(tmp_path / "LeagueClientUx.exe")

        def cmdline(self) -> list[str]:
            return []

    monkeypatch.setattr(
        "lolcoach.lcu.discovery.psutil.process_iter", lambda attrs: iter([FakeProcess()])
    )
    locator = ClientLocator()
    creds = locator.locate()
    assert creds is not None and creds.port == 6000
    assert locator.league_dir == tmp_path
