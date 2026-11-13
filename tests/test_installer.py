"""Instalador para otras personas: copia la app, actualiza sin dejar restos y no se sale
de su carpeta aunque el paquete venga manipulado."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name: str, path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer = load("lolcoach_installer", ROOT / "packaging" / "installer.py")
builder = load("lolcoach_build_installer", ROOT / "scripts" / "build_installer.py")


def fake_app(folder: Path, marker: str = "v1") -> Path:
    (folder / "_internal").mkdir(parents=True)
    (folder / "LoL Coach.exe").write_text(marker, encoding="utf-8")
    (folder / "lolcoach-consola.exe").write_text(marker, encoding="utf-8")
    (folder / "_internal" / "base_library.zip").write_text(marker, encoding="utf-8")
    return folder


def payload_of(app: Path, tmp: Path, name: str = "payload.zip") -> Path:
    payload = tmp / name
    builder.make_payload(app, payload)
    return payload


def test_payload_keeps_the_app_layout(tmp_path: Path) -> None:
    payload = payload_of(fake_app(tmp_path / "app"), tmp_path)
    with zipfile.ZipFile(payload) as archive:
        assert sorted(archive.namelist()) == [
            "LoL Coach.exe", "_internal/base_library.zip", "lolcoach-consola.exe",
        ]  # fmt: skip


def test_extract_installs_and_adds_the_uninstaller(tmp_path: Path) -> None:
    target = tmp_path / "Programs" / "LoL Coach"
    installer.extract(payload_of(fake_app(tmp_path / "app"), tmp_path), target)
    assert (target / "LoL Coach.exe").read_text(encoding="utf-8") == "v1"
    assert (target / "_internal" / "base_library.zip").exists()
    script = (target / "desinstalar.ps1").read_text(encoding="utf-8-sig")
    assert "Uninstall\\LoLCoach" in script and "LoL Coach.lnk" in script
    assert "LoLCoach" not in script.replace("Uninstall\\LoLCoach", "")  # nunca toca tus datos


def test_update_replaces_the_old_version_without_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "LoL Coach"
    installer.extract(payload_of(fake_app(tmp_path / "v1"), tmp_path, "v1.zip"), target)
    (target / "viejo.dll").write_text("x", encoding="utf-8")  # archivo que ya no existe en v2
    installer.extract(payload_of(fake_app(tmp_path / "v2", "v2"), tmp_path, "v2.zip"), target)
    assert (target / "LoL Coach.exe").read_text(encoding="utf-8") == "v2"
    assert not (target / "viejo.dll").exists()
    assert not target.with_name("LoL Coach.nuevo").exists()


def test_incomplete_or_tampered_payloads_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "LoL Coach"
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("leeme.txt", "nada")
    with pytest.raises(installer.InstallError, match="incompleto"):
        installer.extract(empty, target)

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("LoL Coach.exe", "app")
        archive.writestr("../../fuera.txt", "no")
    with pytest.raises(installer.InstallError, match="dañado"):
        installer.extract(evil, target)
    assert not (tmp_path / "fuera.txt").exists() and not target.exists()


def test_install_refuses_while_the_app_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installer, "app_running", lambda _target: True)
    places = installer.Places(target=tmp_path / "LoL Coach", shortcuts=())
    payload = payload_of(fake_app(tmp_path / "app"), tmp_path)
    with pytest.raises(installer.InstallError, match="abierto"):
        installer.install(payload, places, "0.8.1", register=False)
    assert not places.target.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="accesos directos de Windows")
def test_install_creates_working_shortcuts(tmp_path: Path) -> None:
    places = installer.Places(target=tmp_path / "LoL Coach", shortcuts=(tmp_path / "Inicio",))
    payload = payload_of(fake_app(tmp_path / "app"), tmp_path)
    exe = installer.install(payload, places, "0.8.1", register=False)
    assert exe == places.target / "LoL Coach.exe"
    assert (tmp_path / "Inicio" / "LoL Coach.lnk").stat().st_size > 0
