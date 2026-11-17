"""Script de publicación: manifiesto, comprobaciones previas y subida a GitHub (todo simulado)."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import urllib.error
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend

from erizocoach.update.manifest import INSTALLER_NAME, UpdateError

from .test_installer import ROOT, load

publisher = load("erizocoach_publicar_version", ROOT / "scripts" / "publicar_version.py")

TOKEN = "github_pat_SECRETO_123"


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.data[(service, username)]


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeOpener:
    def __init__(self, exists: bool = False) -> None:
        self.exists = exists
        self.requests: list = []

    def __call__(self, request, timeout=None):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        url = request.full_url
        if request.get_method() == "GET":
            if self.exists:
                return FakeResponse(200)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        if url.startswith("https://api.github.com/"):
            return FakeResponse(201, json.dumps({"id": 77}).encode())
        return FakeResponse(201)


def fake_git(tag_exists: bool = True, pushed: bool = True, dirty: bool = False):  # type: ignore[no-untyped-def]
    def run(args: list[str]) -> tuple[int, str]:
        if args[0] == "status":
            return 0, " M archivo.py\n" if dirty else ""
        if args[0] == "rev-parse":
            return (0, "abc\n") if tag_exists else (1, "")
        if args[0] == "ls-remote":
            return 0, "abc\trefs/tags/v1.2.3\n" if pushed else ""
        if args[0] == "tag":
            return 0, "Notas\n"
        return 1, ""

    return run


def test_manifest_describes_the_installer(tmp_path: Path) -> None:
    exe = tmp_path / "Instalar-Erizo-Coach-1.2.3.exe"
    exe.write_bytes(b"instalador" * 100)
    manifest = publisher.build_manifest(exe, "1.2.3", "Novedades", "2026-01-01T00:00:00+00:00")
    assert manifest.version == "1.2.3"
    assert manifest.installer == "Instalar-Erizo-Coach-1.2.3.exe"
    assert INSTALLER_NAME.match(manifest.installer)
    assert manifest.size == 1000
    assert manifest.sha256 == hashlib.sha256(b"instalador" * 100).hexdigest()
    assert manifest.notes == "Novedades"


def test_preflight_lists_problems() -> None:
    assert publisher.preflight("1.2.3", run_git=fake_git()) == []
    problems = publisher.preflight("1.2.3", run_git=fake_git(dirty=True))
    assert len(problems) == 1 and "sin guardar" in problems[0]
    problems = publisher.preflight("1.2.3", run_git=fake_git(tag_exists=False, pushed=False))
    assert len(problems) == 2
    problems = publisher.preflight("1.2.3", run_git=fake_git(pushed=False))
    assert len(problems) == 1 and "origin" in problems[0]


def test_release_notes_use_the_tag_subject() -> None:
    seen: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str]:
        seen.append(args)
        return 0, "Botón de actualizar\n"

    assert publisher.release_notes("v1.2.3", run_git=run) == "Botón de actualizar"
    assert "v1.2.3" in seen[0] and "--format=%(contents:subject)" in seen[0]


def test_release_is_created_with_three_assets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    files = {}
    for name, content in (
        ("erizocoach-version.json", b"{}"),
        ("erizocoach-version.json.sig", b"c2ln"),
        ("Instalar-Erizo-Coach-1.2.3.exe", b"MZ"),
    ):
        files[name] = tmp_path / name
        files[name].write_bytes(content)
    opener = FakeOpener()
    with caplog.at_level(logging.DEBUG):
        github = publisher.GitHubReleases(TOKEN, opener=opener)
        assert github.release_exists("v1.2.3") is False
        release_id = github.create_release("v1.2.3", "Erizo Coach 1.2.3", "Novedades")
        for name, path in files.items():
            github.upload_asset(release_id, path, name, "application/octet-stream")
    assert release_id == 77
    post = opener.requests[1]
    assert post.full_url == "https://api.github.com/repos/ericperezortuno/ErizoCoach/releases"
    assert json.loads(post.data) == {
        "tag_name": "v1.2.3",
        "name": "Erizo Coach 1.2.3",
        "body": "Novedades",
    }
    uploads = opener.requests[2:]
    assert [r.full_url for r in uploads] == [
        f"https://uploads.github.com/repos/ericperezortuno/ErizoCoach/releases/77/assets?name={n}"
        for n in files
    ]
    for request in opener.requests:
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        assert TOKEN not in request.full_url
        assert request.get_header("User-agent") == "ErizoCoach-publicar"
    assert TOKEN not in caplog.text


def test_existing_release_is_not_overwritten() -> None:
    opener = FakeOpener(exists=True)
    github = publisher.GitHubReleases(TOKEN, opener=opener)
    assert github.release_exists("v1.2.3") is True
    assert all(r.get_method() == "GET" for r in opener.requests)


def test_publish_stops_when_the_release_exists(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    key_file = tmp_path / "signing_key.py"
    key_file.write_text('PUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    public_hex = publisher.create_signing_key(key_file, store=store)
    store.set_password("ErizoCoach-publicar", "github_token", TOKEN)
    built: list[int] = []
    code = publisher.publish(
        "1.2.3",
        store=store,
        run_git=fake_git(),
        run_build=lambda: built.append(1),
        github_factory=lambda token: publisher.GitHubReleases(token, opener=FakeOpener(True)),
        public_key=bytes.fromhex(public_hex),
    )
    assert code == 1 and built == []
    assert "ya existe" in capsys.readouterr().out


def test_create_key_writes_public_key_and_refuses_a_second_time(tmp_path: Path) -> None:
    store = MemoryKeyring()
    key_file = tmp_path / "signing_key.py"
    key_file.write_text('"""Doc."""\nPUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    public_hex = publisher.create_signing_key(key_file, store=store)
    assert len(bytes.fromhex(public_hex)) == 32
    text = key_file.read_text("utf-8")
    assert f'PUBLIC_KEY: bytes = bytes.fromhex("{public_hex}")' in text
    private = store.get_password("ErizoCoach-publicar", "clave_firma")
    assert private is not None and private not in text
    with pytest.raises(publisher.PublishError, match="Ya existe"):
        publisher.create_signing_key(key_file, store=store)


def test_store_token_rejects_empty_and_never_echoes(capsys) -> None:  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    with pytest.raises(publisher.PublishError):
        publisher.store_token(store=store, ask=lambda _prompt: "  ")
    publisher.store_token(store=store, ask=lambda _prompt: TOKEN)
    assert store.get_password("ErizoCoach-publicar", "github_token") == TOKEN
    assert TOKEN not in capsys.readouterr().out


def test_publish_end_to_end(tmp_path: Path) -> None:
    store = MemoryKeyring()
    key_file = tmp_path / "signing_key.py"
    key_file.write_text('PUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    public_hex = publisher.create_signing_key(key_file, store=store)
    store.set_password("ErizoCoach-publicar", "github_token", TOKEN)
    version = publisher.__version__
    dist = tmp_path / "dist"
    dist.mkdir()

    def build() -> None:
        (dist / f"Instalar Erizo Coach {version}.exe").write_bytes(b"MZ" * 50)

    opener = FakeOpener()
    verified: list[str] = []

    def check(checked_version: str, public_key: bytes) -> None:
        assert public_key == bytes.fromhex(public_hex)
        verified.append(checked_version)

    code = publisher.publish(
        version,
        store=store,
        run_git=fake_git(),
        run_build=build,
        github_factory=lambda token: publisher.GitHubReleases(token, opener=opener),
        public_key=bytes.fromhex(public_hex),
        dist=dist,
        check_published=check,
    )
    assert code == 0 and verified == [version]
    names = [r.full_url.rsplit("name=", 1)[-1] for r in opener.requests[2:]]
    assert names == [
        "erizocoach-version.json",
        "erizocoach-version.json.sig",
        f"Instalar-Erizo-Coach-{version}.exe",
    ]


def test_publish_refuses_without_public_key(capsys) -> None:  # type: ignore[no-untyped-def]
    code = publisher.publish("1.2.3", store=MemoryKeyring(), public_key=b"")
    assert code == 1
    assert "--crear-clave" in capsys.readouterr().out


def test_publish_refuses_a_key_that_does_not_match(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    (tmp_path / "a.py").write_text('PUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    (tmp_path / "b.py").write_text('PUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    publisher.create_signing_key(tmp_path / "a.py", store=store)
    other_hex = publisher.create_signing_key(tmp_path / "b.py", store=MemoryKeyring())
    code = publisher.publish("1.2.3", store=store, public_key=bytes.fromhex(other_hex))
    assert code == 1
    assert "no corresponde" in capsys.readouterr().out


def test_verify_published_retries_then_fails() -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def fetch() -> tuple[bytes, bytes]:
        calls.append(1)
        raise UpdateError("todavía no")

    with pytest.raises(publisher.PublishError):
        publisher.verify_published("1.2.3", b"k" * 32, fetch=fetch, sleep=sleeps.append, attempts=3)
    assert len(calls) == 3 and len(sleeps) == 2
