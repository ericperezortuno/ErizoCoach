"""Servicio de actualización: aviso, descarga verificada, instalación y fases seguras."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from erizocoach.bus import EventBus
from erizocoach.config import Settings, load_settings
from erizocoach.events import (
    ClientConnected,
    ClientDisconnected,
    GameflowPhaseChanged,
    UpdateRequested,
    UpdateState,
    UpdateStatus,
)
from erizocoach.models import ClientInfo, GameflowPhase
from erizocoach.update import service as service_module
from erizocoach.update.download import MANIFEST_URL, installer_url
from erizocoach.update.manifest import Manifest, UpdateError, encode_manifest, sign
from erizocoach.update.service import UpdateService, disabled_reason, should_check

SHA = "b" * 64
CURRENT = "0.22.0"


def _keys() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, public


PRIVATE, PUBLIC = _keys()


def _manifest(version: str = "0.23.0") -> Manifest:
    return Manifest(
        version=version,
        installer=f"Instalar-Erizo-Coach-{version}.exe",
        size=1234,
        sha256=SHA,
        notes="Novedades",
        published="2026-11-17",
    )


class FakeDownloader:
    def __init__(self, manifest: Manifest | None, *, signature: bytes | None = None) -> None:
        self.data = encode_manifest(manifest) if manifest else None
        if signature is None and self.data is not None:
            signature = sign(self.data, PRIVATE)
        self.signature = signature
        self.files: list[Path] = []
        self.fail: Exception | None = None
        self.on_download: Callable[[], Awaitable[None]] | None = None
        self.requests: list[tuple[str, int, str]] = []
        self.manifest_fetches = 0

    async def fetch(self, url: str, limit: int) -> bytes | None:
        if url == MANIFEST_URL:
            self.manifest_fetches += 1
            return self.data
        return self.signature

    async def to_file(self, url, path, size, sha256, progress) -> None:  # type: ignore[no-untyped-def]
        self.requests.append((url, size, sha256))
        path.write_bytes(b"MZ")  # a medias
        try:
            await progress(50)  # como el Downloader real: porcentaje
            if self.on_download is not None:
                await self.on_download()
            if self.fail is not None:
                raise self.fail
            await progress(100)
        except BaseException:
            path.unlink(missing_ok=True)  # el Downloader real no deja archivos si falla
            raise
        self.files.append(path)


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        downloader: FakeDownloader,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.bus = EventBus()
        self.statuses: list[UpdateStatus] = []
        self.calls: list[str] = []
        self.launched: list[Path] = []
        self.launch_error: OSError | None = None
        self.download_dir = tmp_path / "descargas"
        self.bus.subscribe(UpdateStatus, self._on_status)
        self.service = UpdateService(
            self.bus,
            current_version=CURRENT,
            public_key=PUBLIC,
            downloader=downloader,  # type: ignore[arg-type]
            download_dir=self.download_dir,
            launch_installer=self._launch,
            quit_app=lambda: self.calls.append("quit"),
            sleep=sleep,
        )

    def _on_status(self, status: UpdateStatus) -> None:
        self.statuses.append(status)
        self.calls.append(status.state.value)

    def _launch(self, path: Path) -> None:
        if self.launch_error is not None:
            raise self.launch_error
        self.launched.append(path)
        self.calls.append("launch")

    async def phase(self, phase: GameflowPhase) -> None:
        await self.bus.publish(ClientConnected(ClientInfo("16.1", "EUW", "es_ES")))
        await self.bus.publish(GameflowPhaseChanged(GameflowPhase.NONE, phase))

    async def announce(self) -> None:
        assert await self.service.check() is not None

    async def request(self) -> None:
        """Pide la actualización y espera a que termine."""
        await self.bus.publish(UpdateRequested())
        task = self.service.update_task
        if task is not None:
            await task


async def test_newer_version_is_announced(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(_manifest("0.23.0")))
    manifest = await h.service.check()
    assert manifest is not None and manifest.version == "0.23.0"
    assert h.statuses == [
        UpdateStatus(version="0.23.0", notes="Novedades", state=UpdateState.AVAILABLE)
    ]


@pytest.mark.parametrize("version", ["0.22.0", "0.21.9", "0.9.0"])
async def test_same_or_older_version_is_ignored(tmp_path: Path, version: str) -> None:
    h = Harness(tmp_path, FakeDownloader(_manifest(version)))
    assert await h.service.check() is None
    assert h.statuses == []


async def test_bad_signature_is_ignored(tmp_path: Path) -> None:
    other_private, _ = _keys()
    data = encode_manifest(_manifest())
    h = Harness(tmp_path, FakeDownloader(_manifest(), signature=sign(data, other_private)))
    assert await h.service.check() is None
    assert h.statuses == []


async def test_missing_manifest_is_silent(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(None))
    assert await h.service.check() is None
    assert h.statuses == []


async def test_network_error_in_check_is_silent(tmp_path: Path) -> None:
    class Broken(FakeDownloader):
        async def fetch(self, url: str, limit: int) -> bytes | None:
            raise UpdateError("sin red")

    h = Harness(tmp_path, Broken(_manifest()))
    assert await h.service.check() is None
    assert h.statuses == []


async def test_update_downloads_verifies_launches_and_quits(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    h.calls.clear()
    await h.request()
    assert h.calls == ["downloading"] * 3 + ["launch", "installing", "quit"]
    downloading = [s for s in h.statuses if s.state is UpdateState.DOWNLOADING]
    assert [s.progress for s in downloading] == [0, 50, 100]
    manifest = _manifest()
    assert downloader.requests == [(installer_url(manifest), manifest.size, manifest.sha256)]
    (path,) = h.launched
    assert path.parent == h.download_dir
    assert path.name == "Instalar-Erizo-Coach-0.23.0.exe"
    assert path.exists()


async def test_request_without_announced_update_does_nothing(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(_manifest()))
    await h.request()
    assert h.statuses == [] and h.calls == []


async def test_request_in_unsafe_phase_is_refused(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    await h.phase(GameflowPhase.CHAMP_SELECT)
    h.calls.clear()
    await h.request()
    assert downloader.files == [] and "quit" not in h.calls
    assert h.statuses[-1].state is UpdateState.FAILED
    assert h.statuses[-1].error


@pytest.mark.parametrize(
    "phase", [GameflowPhase.NONE, GameflowPhase.LOBBY, GameflowPhase.END_OF_GAME]
)
async def test_safe_phases_allow_update(tmp_path: Path, phase: GameflowPhase) -> None:
    h = Harness(tmp_path, FakeDownloader(_manifest()))
    await h.announce()
    await h.phase(phase)
    await h.request()
    assert h.calls[-1] == "quit"


async def test_disconnected_is_safe(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(_manifest()))
    assert h.service.safe
    await h.phase(GameflowPhase.IN_PROGRESS)
    assert not h.service.safe
    await h.bus.publish(ClientDisconnected())
    assert h.service.safe


async def test_phase_turns_unsafe_during_download(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    await h.phase(GameflowPhase.LOBBY)

    async def start_champ_select() -> None:
        await h.bus.publish(GameflowPhaseChanged(GameflowPhase.LOBBY, GameflowPhase.CHAMP_SELECT))

    downloader.on_download = start_champ_select
    await h.request()
    assert h.launched == [] and "quit" not in h.calls
    assert h.statuses[-1].state is UpdateState.FAILED
    assert h.statuses[-1].error == service_module.UNSAFE_MESSAGE
    assert "menú o en la sala" in service_module.UNSAFE_MESSAGE
    assert list(h.download_dir.glob("*.exe")) == []


async def test_failed_download_reports_and_allows_retry(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    downloader.fail = UpdateError("Descarga cortada")
    await h.request()
    assert h.statuses[-1].state is UpdateState.FAILED
    assert h.statuses[-1].error and h.launched == []
    downloader.fail = None
    await h.request()
    assert h.calls[-1] == "quit"


async def test_launch_failure_reports_and_does_not_quit(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(_manifest()))
    await h.announce()
    h.launch_error = OSError("bloqueado")
    await h.request()
    assert h.statuses[-1].state is UpdateState.FAILED
    assert h.statuses[-1].error and "quit" not in h.calls
    assert list(h.download_dir.glob("*.exe")) == []  # el instalador descargado se borra


async def test_run_checks_after_first_delay_then_every_interval(tmp_path: Path) -> None:
    events: list[str] = []
    downloader = FakeDownloader(None)

    async def fake_sleep(seconds: float) -> None:
        events.append(f"sleep {seconds}")
        events.append(f"fetches {downloader.manifest_fetches}")
        if events.count("sleep 21600") == 2:
            raise asyncio.CancelledError

    h = Harness(tmp_path, downloader, sleep=fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await h.service.run()
    assert events == [
        "sleep 30.0", "fetches 0",
        "sleep 21600", "fetches 1",
        "sleep 21600", "fetches 2",
    ]  # fmt: skip


def test_should_check_only_in_frozen_app_with_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert not should_check(settings, PUBLIC)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert should_check(settings, PUBLIC)
    assert not should_check(settings, b"")
    assert not should_check(Settings(data_dir=tmp_path, update_check=False), PUBLIC)


def test_update_check_setting(tmp_path: Path) -> None:
    settings, _ = load_settings(data_dir=tmp_path)
    assert settings.update_check is True
    (tmp_path / "config.toml").write_text("update_check = false\n")
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.update_check is False
    assert warnings == []


def test_launch_installer_detached_passes_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        captured["args"], captured["kwargs"] = args, kwargs

    monkeypatch.setattr(service_module.subprocess, "Popen", fake_popen)
    service_module.launch_installer_detached(Path("C:/x/Instalar.exe"))
    # Desde su propia carpeta: nunca con la de la app como carpeta de trabajo.
    assert captured["kwargs"]["cwd"] == str(Path("C:/x"))  # type: ignore[index]
    args = captured["args"]
    assert args == [str(Path("C:/x/Instalar.exe")), "--actualizar", str(os.getpid())]
    flags = captured["kwargs"]["creationflags"]  # type: ignore[index]
    assert flags & 0x00000008 and flags & 0x00000200


async def test_request_returns_before_download_finishes(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    gate = asyncio.Event()

    async def wait_gate() -> None:
        await gate.wait()

    downloader.on_download = wait_gate
    await h.bus.publish(UpdateRequested())  # vuelve sin esperar a la descarga
    task = h.service.update_task
    assert task is not None and not task.done()
    assert h.launched == []
    gate.set()
    await task
    assert h.calls[-1] == "quit"


async def test_requests_during_update_are_ignored(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    gate = asyncio.Event()

    async def wait_gate() -> None:
        await gate.wait()

    downloader.on_download = wait_gate
    await h.bus.publish(UpdateRequested())
    first = h.service.update_task
    await h.bus.publish(UpdateRequested())
    assert h.service.update_task is first
    gate.set()
    await h.request()
    assert len(downloader.files) == 1 and h.calls.count("quit") == 1


async def test_check_during_update_does_not_reannounce(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    gate = asyncio.Event()

    async def wait_gate() -> None:
        await gate.wait()

    downloader.on_download = wait_gate
    await h.bus.publish(UpdateRequested())
    await asyncio.sleep(0)
    before = len(h.statuses)
    assert await h.service.check() is not None
    assert len(h.statuses) == before
    gate.set()
    await h.request()


async def test_connected_without_known_phase_is_not_safe(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    await h.bus.publish(ClientConnected(ClientInfo("16.1", "EUW", "es_ES")))
    assert not h.service.safe
    await h.request()
    assert downloader.files == [] and "quit" not in h.calls
    assert h.statuses[-1].state is UpdateState.FAILED


async def test_cancelling_run_cancels_update_in_flight(tmp_path: Path) -> None:
    downloader = FakeDownloader(_manifest())
    forever = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await forever.wait()

    h = Harness(tmp_path, downloader, sleep=sleep)
    await h.announce()
    started = asyncio.Event()

    async def block() -> None:
        started.set()
        await asyncio.Event().wait()

    downloader.on_download = block
    runner = asyncio.create_task(h.service.run())
    await h.bus.publish(UpdateRequested())
    await started.wait()
    task = h.service.update_task
    assert task is not None
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    assert task.cancelled()
    assert h.launched == [] and "quit" not in h.calls
    assert list(h.download_dir.glob("*.exe")) == []


async def test_unexpected_error_in_update_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader)
    await h.announce()
    downloader.fail = RuntimeError("raro")  # type: ignore[assignment]
    await h.bus.publish(UpdateRequested())
    task = h.service.update_task
    assert task is not None
    await asyncio.wait([task])
    assert "Fallo inesperado al descargar la actualización" in caplog.text
    assert h.statuses[-1].state is UpdateState.FAILED
    assert h.statuses[-1].error and "raro" not in h.statuses[-1].error
    assert h.launched == [] and "quit" not in h.calls
    assert list(h.download_dir.glob("*.exe")) == []


# --- Registro de lo que hace el servicio ----------------------------------------------------


async def test_check_logs_each_outcome(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="erizocoach.update.service")
    await Harness(tmp_path, FakeDownloader(None)).service.check()
    assert "No hay manifiesto de versión publicado (404)" in caplog.text
    caplog.clear()
    await Harness(tmp_path, FakeDownloader(_manifest("0.22.0"))).service.check()
    assert "Última versión publicada 0.22.0; la instalada es 0.22.0" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    caplog.clear()
    await Harness(tmp_path, FakeDownloader(_manifest("0.23.0"))).service.check()
    (info,) = [r for r in caplog.records if r.levelno == logging.INFO]
    assert "Nueva versión 0.23.0 disponible" in info.getMessage()


async def test_launching_the_installer_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="erizocoach.update.service")
    h = Harness(tmp_path, FakeDownloader(_manifest()))
    await h.announce()
    await h.request()
    assert "Actualizando a 0.23.0: se abre el instalador y Erizo Coach se cierra" in caplog.text


def test_disabled_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert "desde el código" in (disabled_reason(settings, PUBLIC) or "")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert disabled_reason(settings, PUBLIC) is None
    off = Settings(data_dir=tmp_path, update_check=False)
    assert "update_check" in (disabled_reason(off, PUBLIC) or "")
    assert "clave pública" in (disabled_reason(settings, b"") or "")


# --- La app no se cierra tras lanzar el instalador -----------------------------------------


async def test_app_that_does_not_quit_reports_failure_and_allows_retry(tmp_path: Path) -> None:
    waits: list[float] = []

    async def instant(seconds: float) -> None:
        waits.append(seconds)

    downloader = FakeDownloader(_manifest())
    h = Harness(tmp_path, downloader, sleep=instant)
    await h.announce()
    await h.request()
    watch = h.service.quit_watch_task
    assert watch is not None
    await watch
    assert waits == [service_module.QUIT_TIMEOUT] and service_module.QUIT_TIMEOUT >= 45
    last = h.statuses[-1]
    assert last.state is UpdateState.FAILED
    assert last.error == service_module.NOT_CLOSED_MESSAGE
    assert "vuelve a abrirlo" in last.error
    h.calls.clear()
    await h.request()  # un segundo intento funciona con normalidad
    assert h.calls[-1] == "quit" and len(downloader.requests) == 2


async def test_app_that_quits_in_time_is_not_reported(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def hold(_seconds: float) -> None:
        await gate.wait()

    h = Harness(tmp_path, FakeDownloader(_manifest()), sleep=hold)
    await h.announce()
    await h.request()
    watch = h.service.quit_watch_task
    assert watch is not None and not watch.done()
    await h.service._cancel_update()  # la app se cierra: la espera se cancela
    assert watch.cancelled()
    assert h.statuses[-1].state is UpdateState.INSTALLING


# --- Instalador sobrante --------------------------------------------------------------------


async def test_first_check_deletes_leftover_installer(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(None))
    h.download_dir.mkdir()
    leftover = h.download_dir / "Instalar-Erizo-Coach-0.22.0.exe"
    leftover.write_bytes(b"MZ")
    await h.service.check()
    assert not leftover.exists()


async def test_only_the_first_check_cleans(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeDownloader(None))
    await h.service.check()
    h.download_dir.mkdir()
    later = h.download_dir / "en-uso.exe"
    later.write_bytes(b"MZ")
    await h.service.check()
    assert later.exists()
