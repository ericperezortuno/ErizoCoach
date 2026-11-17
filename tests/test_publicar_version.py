"""Script de publicación: manifiesto, comprobaciones previas y subida a GitHub (todo simulado)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import subprocess
import sys
import urllib.error
from pathlib import Path

import keyring.errors
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from keyring.backend import KeyringBackend

from erizocoach.update.manifest import INSTALLER_NAME, Manifest, UpdateError, verify_manifest

from .test_installer import ROOT, load

publisher = load("erizocoach_publicar_version", ROOT / "scripts" / "publicar_version.py")

TOKEN = "github_pat_SECRETO_123"
SERVICE = "ErizoCoach-publicar"
API = "https://api.github.com/repos/eriizzo/ErizoCoach/releases"
UPLOADS = "https://uploads.github.com/repos/eriizzo/ErizoCoach/releases"


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
    """GitHub simulado: guarda cada petición y el cuerpo que se subió."""

    def __init__(self, exists: bool = False, fail_upload: str | None = None) -> None:
        self.exists = exists
        self.fail_upload = fail_upload
        self.requests: list = []
        self.bodies: dict[str, bytes] = {}  # nombre del asset -> bytes subidos
        self.draft = False
        self.published = False
        self.deleted = False

    def __call__(self, request, timeout=None):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        url, method = request.full_url, request.get_method()
        if method == "GET":
            if self.exists:
                return FakeResponse(200)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        if method == "PATCH":
            self.published = json.loads(request.data) == {"draft": False}
            return FakeResponse(200)
        if method == "DELETE":
            self.deleted = True
            return FakeResponse(204)
        if url.startswith(API):
            self.draft = json.loads(request.data)["draft"]
            return FakeResponse(201, json.dumps({"id": 77}).encode())
        name = url.rsplit("name=", 1)[-1]
        if self.fail_upload and self.fail_upload in name:
            raise urllib.error.HTTPError(url, 500, "Error", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        self.bodies[name] = request.data
        return FakeResponse(201)

    def calls(self) -> list[str]:
        """Resumen en orden: «POST release», «POST asset <nombre>», «PATCH», «DELETE»..."""
        out = []
        for r in self.requests:
            if r.get_method() == "POST" and r.full_url.startswith(UPLOADS):
                out.append("asset " + r.full_url.rsplit("name=", 1)[-1])
            else:
                out.append(r.get_method())
        return out


def fake_git(
    tag_exists: bool = True,
    pushed: bool = True,
    dirty: bool = False,
    tag_at_head: bool = True,
    remote_commit: str | None = None,
):  # type: ignore[no-untyped-def]
    def run(args: list[str]) -> tuple[int, str]:
        if args[0] == "status":
            return 0, " M archivo.py\n" if dirty else ""
        if args[0] == "rev-parse" and args[-1] == "HEAD":
            return 0, "abc\n"
        if args[0] == "rev-parse":
            return (0, "abc\n" if tag_at_head else "def\n") if tag_exists else (1, "")
        if args[0] == "ls-remote":
            tagged = remote_commit or ("abc" if tag_at_head else "def")
            return (0, f"{tagged}\t{args[-1]}\n") if pushed else (0, "")
        if args[0] == "tag":
            return 0, "Notas\n"
        return 1, ""

    return run


def make_key(
    tmp_path: Path, store: MemoryKeyring, name: str = "signing_key.py"
) -> tuple[Path, str]:
    key_file = tmp_path / name
    key_file.write_text('PUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    return key_file, publisher.create_signing_key(key_file, store=store, current_public=b"")


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


def test_preflight_requires_the_tag_at_head() -> None:
    problems = publisher.preflight("1.2.3", run_git=fake_git(tag_at_head=False))
    assert len(problems) == 1 and "no apunta al commit actual" in problems[0]
    assert publisher.preflight("1.2.3", run_git=fake_git(tag_at_head=True)) == []


def test_preflight_requires_origin_tag_at_the_same_commit() -> None:
    problems = publisher.preflight("1.2.3", run_git=fake_git(remote_commit="zzz"))
    assert len(problems) == 1 and "origin apunta a otro commit" in problems[0]


def test_preflight_falls_back_to_lightweight_remote_tags() -> None:
    inner = fake_git()

    def run(args: list[str]) -> tuple[int, str]:
        if args[0] == "ls-remote" and args[-1].endswith("^{}"):
            return 0, ""  # etiqueta ligera: no hay entrada «pelada»
        return inner(args)

    assert publisher.preflight("1.2.3", run_git=run) == []


def test_release_notes_use_the_tag_subject() -> None:
    seen: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str]:
        seen.append(args)
        return 0, "Botón de actualizar\n"

    assert publisher.release_notes("v1.2.3", run_git=run) == "Botón de actualizar"
    assert "v1.2.3" in seen[0] and "--format=%(contents:subject)" in seen[0]


def test_release_is_created_as_draft_with_three_assets(
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
    types = {
        "erizocoach-version.json": "application/json",
        "erizocoach-version.json.sig": "text/plain",
        "Instalar-Erizo-Coach-1.2.3.exe": "application/vnd.microsoft.portable-executable",
    }
    opener = FakeOpener()
    with caplog.at_level(logging.DEBUG):
        github = publisher.GitHubReleases(TOKEN, opener=opener)
        assert github.release_exists("v1.2.3") is False
        release_id = github.create_release("v1.2.3", "Erizo Coach 1.2.3", "Novedades")
        for name, path in files.items():
            github.upload_asset(release_id, path, name, types[name])
    assert release_id == 77
    post = opener.requests[1]
    assert post.full_url == API
    assert json.loads(post.data) == {
        "tag_name": "v1.2.3",
        "name": "Erizo Coach 1.2.3",
        "body": "Novedades",
        "draft": True,
    }
    uploads = opener.requests[2:]
    assert [r.full_url for r in uploads] == [f"{UPLOADS}/77/assets?name={n}" for n in files]
    for request, name in zip(uploads, files, strict=True):
        assert request.get_header("Content-type") == types[name]
    for request in opener.requests:
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        assert request.get_header("Accept") == "application/vnd.github+json"
        assert request.get_header("X-github-api-version") == "2022-11-28"
        assert TOKEN not in request.full_url
        assert request.get_header("User-agent") == "ErizoCoach-publicar"
    assert TOKEN not in caplog.text


def test_existing_release_is_not_overwritten() -> None:
    opener = FakeOpener(exists=True)
    github = publisher.GitHubReleases(TOKEN, opener=opener)
    assert github.release_exists("v1.2.3") is True
    assert all(r.get_method() == "GET" for r in opener.requests)


def test_network_errors_become_publish_errors() -> None:
    def broken(request, timeout=None):  # type: ignore[no-untyped-def]
        raise ConnectionResetError(f"corte con {request.headers}")

    github = publisher.GitHubReleases(TOKEN, opener=broken)
    with pytest.raises(publisher.PublishError) as info:
        github.release_exists("v1.2.3")
    assert TOKEN not in str(info.value)

    def odd(request, timeout=None):  # type: ignore[no-untyped-def]
        return FakeResponse(302)

    with pytest.raises(publisher.PublishError):
        publisher.GitHubReleases(TOKEN, opener=odd).release_exists("v1.2.3")


def test_publish_stops_when_the_release_exists(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    _, public_hex = make_key(tmp_path, store)
    store.set_password(SERVICE, "github_token", TOKEN)
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


def test_create_key_writes_ruff_clean_module_and_refuses_a_second_time(tmp_path: Path) -> None:
    store = MemoryKeyring()
    key_file = tmp_path / "signing_key.py"
    key_file.write_text('"""Doc."""\nPUBLIC_KEY: bytes = b""\n', encoding="utf-8")
    public_hex = publisher.create_signing_key(key_file, store=store, current_public=b"")
    assert len(bytes.fromhex(public_hex)) == 32
    text = key_file.read_text("utf-8")
    assert all(len(line) <= 100 for line in text.splitlines())
    namespace: dict = {}
    exec(compile(text, str(key_file), "exec"), namespace)
    assert namespace["PUBLIC_KEY"] == bytes.fromhex(public_hex)
    private = store.get_password(SERVICE, "clave_firma")
    assert private is not None and private not in text
    for command in (["format", "--check"], ["check"]):
        config = str(ROOT / "pyproject.toml")
        result = subprocess.run(
            [sys.executable, "-m", "ruff", *command, "--config", config, str(key_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(publisher.PublishError, match="Ya existe"):
        publisher.create_signing_key(key_file, store=store, current_public=b"")


def test_create_key_refuses_when_the_project_already_has_a_public_key(tmp_path: Path) -> None:
    store = MemoryKeyring()
    key_file = tmp_path / "signing_key.py"
    key_file.write_text("original\n", encoding="utf-8")
    with pytest.raises(publisher.PublishError, match="rotarla"):
        publisher.create_signing_key(key_file, store=store, current_public=b"k" * 32)
    assert key_file.read_text("utf-8") == "original\n" and not store.data


def test_failed_keyring_write_leaves_the_key_file_untouched(tmp_path: Path) -> None:
    class Refusing(MemoryKeyring):
        def set_password(self, service: str, username: str, password: str) -> None:
            raise keyring.errors.PasswordSetError("no")

    key_file = tmp_path / "signing_key.py"
    key_file.write_text("original\n", encoding="utf-8")
    with pytest.raises(keyring.errors.PasswordSetError):
        publisher.create_signing_key(key_file, store=Refusing(), current_public=b"")
    assert key_file.read_text("utf-8") == "original\n"


def test_store_token_rejects_empty_and_never_echoes(capsys) -> None:  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    with pytest.raises(publisher.PublishError):
        publisher.store_token(store=store, ask=lambda _prompt: "  ")
    publisher.store_token(store=store, ask=lambda _prompt: TOKEN)
    assert store.get_password(SERVICE, "github_token") == TOKEN
    assert TOKEN not in capsys.readouterr().out


def run_publish(tmp_path: Path, opener: FakeOpener, **extra):  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    _, public_hex = make_key(tmp_path, store)
    store.set_password(SERVICE, "github_token", TOKEN)
    version = publisher.__version__
    dist = tmp_path / "dist"
    dist.mkdir()
    verified: list[str] = []

    def build() -> None:
        (dist / f"Instalar Erizo Coach {version}.exe").write_bytes(b"MZ" * 50)

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
        **extra,
    )
    return code, version, public_hex, verified


def test_publish_end_to_end(tmp_path: Path) -> None:
    opener = FakeOpener()
    code, version, public_hex, verified = run_publish(tmp_path, opener)
    assert code == 0 and verified == [version]
    installer_name = f"Instalar-Erizo-Coach-{version}.exe"
    # borrador -> instalador -> firma -> manifiesto -> publicar
    assert opener.calls() == [
        "GET",
        "POST",
        f"asset {installer_name}",
        "asset erizocoach-version.json.sig",
        "asset erizocoach-version.json",
        "PATCH",
    ]
    assert opener.draft is True and opener.published is True and not opener.deleted
    manifest = verify_manifest(
        opener.bodies["erizocoach-version.json"],
        opener.bodies["erizocoach-version.json.sig"],
        bytes.fromhex(public_hex),
    )
    installer = opener.bodies[installer_name]
    assert manifest.version == version and manifest.installer == installer_name
    assert manifest.size == len(installer)
    assert manifest.sha256 == hashlib.sha256(installer).hexdigest()
    assert manifest.notes == "Notas"
    assert base64.b64decode(opener.bodies["erizocoach-version.json.sig"], validate=True)


def test_publish_verifies_the_signature_locally_before_touching_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    def broken(*_args: object) -> None:
        raise UpdateError("firma no válida")

    monkeypatch.setattr(publisher, "verify_manifest", broken)
    opener = FakeOpener()
    code, _, _, verified = run_publish(tmp_path, opener)
    out = capsys.readouterr().out
    assert code == 1 and verified == []
    assert opener.calls() == ["GET"]  # solo la consulta previa: no se crea ni se sube nada
    assert "no se ha subido nada" in out


def test_failed_upload_deletes_the_draft(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    opener = FakeOpener(fail_upload="json.sig")
    code, _, _, verified = run_publish(tmp_path, opener)
    out = capsys.readouterr().out
    assert code == 1 and verified == []
    assert "PATCH" not in opener.calls() and opener.published is False
    assert opener.calls()[-1] == "DELETE" and opener.deleted
    assert "NO se ha publicado" in out
    assert "bórralo a mano" not in out
    assert "erizocoach-version.json" not in opener.bodies


def test_failed_upload_and_failed_delete_tells_to_clean_by_hand(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    class NoDelete(FakeOpener):
        def __call__(self, request, timeout=None):  # type: ignore[no-untyped-def]
            if request.get_method() == "DELETE":
                raise urllib.error.HTTPError(request.full_url, 403, "No", {}, io.BytesIO(b""))  # type: ignore[arg-type]
            return super().__call__(request, timeout)

    code, _, _, _ = run_publish(tmp_path, NoDelete(fail_upload="Instalar"))
    out = capsys.readouterr().out
    assert code == 1
    assert "NO se ha publicado" in out and "bórralo a mano" in out


def test_publish_refuses_without_public_key(capsys) -> None:  # type: ignore[no-untyped-def]
    code = publisher.publish("1.2.3", store=MemoryKeyring(), public_key=b"")
    assert code == 1
    assert "--crear-clave" in capsys.readouterr().out


def test_publish_refuses_a_key_that_does_not_match(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    store = MemoryKeyring()
    make_key(tmp_path, store, "a.py")
    _, other_hex = make_key(tmp_path, MemoryKeyring(), "b.py")
    code = publisher.publish("1.2.3", store=store, public_key=bytes.fromhex(other_hex))
    assert code == 1
    assert "no corresponde" in capsys.readouterr().out


def test_publish_turns_keyring_errors_into_a_message(capsys) -> None:  # type: ignore[no-untyped-def]
    class Broken(MemoryKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise keyring.errors.KeyringError("secreto interno")

    code = publisher.publish("1.2.3", store=Broken(), public_key=b"k" * 32)
    out = capsys.readouterr().out
    assert code == 1 and "Administrador de credenciales" in out and "secreto" not in out


def signed(version: str) -> tuple[bytes, bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    raw = private.private_bytes_raw()
    manifest = publisher.encode_manifest(
        Manifest(version, f"Instalar-Erizo-Coach-{version}.exe", 10, "a" * 64, "n", "2026-01-01")
    )
    return manifest, publisher.sign(manifest, raw), public


def test_verify_published_accepts_the_matching_version() -> None:
    manifest, signature, public = signed("1.2.3")
    publisher.verify_published(
        "1.2.3", public, fetch=lambda: (manifest, signature), sleep=lambda _s: None
    )


def test_verify_published_rejects_another_version() -> None:
    manifest, signature, public = signed("1.2.2")
    with pytest.raises(publisher.PublishError, match=r"1.2.2"):
        publisher.verify_published(
            "1.2.3", public, fetch=lambda: (manifest, signature), sleep=lambda _s: None, attempts=2
        )


def test_verify_published_retries_then_fails() -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def fetch() -> tuple[bytes, bytes]:
        calls.append(1)
        raise UpdateError("todavía no")

    with pytest.raises(publisher.PublishError):
        publisher.verify_published("1.2.3", b"k" * 32, fetch=fetch, sleep=sleeps.append, attempts=3)
    assert len(calls) == 3 and len(sleeps) == 2
