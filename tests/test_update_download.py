"""Descargas de la actualización: solo dominios de la lista, con límites y hash."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from erizocoach.update.download import Downloader, allowed_url
from erizocoach.update.manifest import UpdateError

PAYLOAD = bytes(range(256)) * 1000  # 256 000 bytes
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


async def _payload(_request: web.Request) -> web.Response:
    return web.Response(body=PAYLOAD)


async def _missing(_request: web.Request) -> web.Response:
    return web.Response(status=404)


async def _boom(_request: web.Request) -> web.Response:
    return web.Response(status=500)


async def _elsewhere(_request: web.Request) -> web.Response:
    return web.Response(status=302, headers={"Location": "http://evil.example/x"})


async def _relative(_request: web.Request) -> web.Response:
    return web.Response(status=302, headers={"Location": "/payload"})


async def _loop(_request: web.Request) -> web.Response:
    return web.Response(status=302, headers={"Location": "/loop"})


async def _cut(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Length": str(len(PAYLOAD))})
    await response.prepare(request)
    await response.write(PAYLOAD[:1000])
    assert request.transport is not None
    request.transport.close()
    return response


async def _chunked(request: web.Request) -> web.StreamResponse:
    """Sin Content-Length (troceado): solo se puede vigilar el tamaño mientras llega."""
    response = web.StreamResponse()
    response.enable_chunked_encoding()
    await response.prepare(request)
    amount = int(request.query["bytes"])
    await response.write(PAYLOAD[:amount])
    await response.write_eof()
    return response


@pytest.fixture
async def server() -> AsyncIterator[TestServer]:
    app = web.Application()
    app.router.add_get("/payload", _payload)
    app.router.add_get("/missing", _missing)
    app.router.add_get("/boom", _boom)
    app.router.add_get("/elsewhere", _elsewhere)
    app.router.add_get("/relative", _relative)
    app.router.add_get("/loop", _loop)
    app.router.add_get("/cut", _cut)
    app.router.add_get("/chunked", _chunked)
    test_server = TestServer(app, host="127.0.0.1")
    await test_server.start_server()
    yield test_server
    await test_server.close()


def downloader() -> Downloader:
    return Downloader(hosts=("127.0.0.1",), schemes=("http",))


def url(server: TestServer, path: str) -> str:
    return f"http://127.0.0.1:{server.port}{path}"


async def _ignore(_percent: int) -> None:
    return None


def test_allowed_urls() -> None:
    assert allowed_url("https://github.com/eriizzo/ErizoCoach/releases")
    assert allowed_url("https://GitHub.com/x")
    assert allowed_url("https://objects.githubusercontent.com/abc")
    for bad in (
        "http://github.com/x",
        "https://github.com.evil.io/x",
        "https://evilgithub.com/x",
        "https://githubusercontent.com.evil.io/x",
        "https://githubusercontent.com/x",
        "https://github.com@evil.io/x",
        "https://user:pw@github.com/x",
        "ftp://github.com/x",
        "not a url",
    ):
        assert not allowed_url(bad), bad


async def test_redirect_to_other_host_is_refused(server: TestServer) -> None:
    with pytest.raises(UpdateError):
        await downloader().fetch(url(server, "/elsewhere"), 1000)


async def test_relative_redirect_is_followed(server: TestServer) -> None:
    assert await downloader().fetch(url(server, "/relative"), 1_000_000) == PAYLOAD


async def test_too_many_redirects(server: TestServer) -> None:
    with pytest.raises(UpdateError):
        await downloader().fetch(url(server, "/loop"), 1000)


async def test_fetch_returns_none_on_404(server: TestServer) -> None:
    assert await downloader().fetch(url(server, "/missing"), 1000) is None


async def test_fetch_other_errors_raise(server: TestServer) -> None:
    with pytest.raises(UpdateError):
        await downloader().fetch(url(server, "/boom"), 1000)


async def test_fetch_respects_limit(server: TestServer) -> None:
    with pytest.raises(UpdateError):
        await downloader().fetch(url(server, "/payload"), len(PAYLOAD) - 1)
    assert await downloader().fetch(url(server, "/payload"), len(PAYLOAD)) == PAYLOAD


async def test_download_checks_size_and_hash(server: TestServer, tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    seen: list[int] = []

    async def progress(percent: int) -> None:
        seen.append(percent)

    await downloader().to_file(url(server, "/payload"), target, len(PAYLOAD), DIGEST, progress)
    assert target.read_bytes() == PAYLOAD
    assert not (tmp_path / "setup.exe.part").exists()
    assert seen[-1] == 100
    assert seen == sorted(set(seen))  # solo cuando cambia, sin repetir

    for size, digest in (
        (len(PAYLOAD), "0" * 64),  # hash distinto
        (len(PAYLOAD) - 1, DIGEST),  # cuerpo más largo que el declarado
        (len(PAYLOAD) + 1, DIGEST),  # cuerpo más corto que el declarado
    ):
        other = tmp_path / "other.exe"
        with pytest.raises(UpdateError):
            await downloader().to_file(url(server, "/payload"), other, size, digest, _ignore)
        assert not other.exists()
        assert not (tmp_path / "other.exe.part").exists()


async def test_download_http_error_leaves_no_file(server: TestServer, tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    with pytest.raises(UpdateError):
        await downloader().to_file(url(server, "/boom"), target, 10, DIGEST, _ignore)
    assert os.listdir(tmp_path) == []


async def test_download_cut_halfway_leaves_no_file(server: TestServer, tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    with pytest.raises(UpdateError):
        await downloader().to_file(url(server, "/cut"), target, len(PAYLOAD), DIGEST, _ignore)
    assert os.listdir(tmp_path) == []


async def test_chunked_fetch_respects_limit(server: TestServer) -> None:
    chunked = url(server, f"/chunked?bytes={len(PAYLOAD)}")
    with pytest.raises(UpdateError, match="demasiado grande"):
        await downloader().fetch(chunked, len(PAYLOAD) - 1)
    assert await downloader().fetch(chunked, len(PAYLOAD)) == PAYLOAD


async def test_chunked_download_too_long_leaves_no_file(server: TestServer, tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    chunked = url(server, f"/chunked?bytes={len(PAYLOAD)}")
    with pytest.raises(UpdateError, match="más grande"):
        await downloader().to_file(chunked, target, len(PAYLOAD) - 1, DIGEST, _ignore)
    assert os.listdir(tmp_path) == []


async def test_chunked_download_too_short_leaves_no_file(
    server: TestServer, tmp_path: Path
) -> None:
    target = tmp_path / "setup.exe"
    chunked = url(server, f"/chunked?bytes={len(PAYLOAD) - 1}")
    with pytest.raises(UpdateError, match="incompleto"):
        await downloader().to_file(chunked, target, len(PAYLOAD), DIGEST, _ignore)
    assert os.listdir(tmp_path) == []
