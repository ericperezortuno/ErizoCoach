"""Segundo plano: icono en la bandeja de Windows y ventana que se esconde y vuelve."""

from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from lolcoach.bus import EventBus
from lolcoach.config import Settings
from lolcoach.ui import desktop, tray
from lolcoach.ui.desktop import BACKGROUND_NOTICE, DesktopWindow
from lolcoach.ui.tray import TrayIcon, default_icon_path, signal_running
from lolcoach.ui.web.presenter import UiPresenter

from .gamedata_fixture import loaded_gamedata

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="bandeja de Windows")


# --- Icono real de Windows ---------------------------------------------------------------


def unique_class() -> str:
    """Nombre propio en cada prueba: nunca se confunde con un Erizo Coach de verdad abierto."""
    return f"LoLCoachTrayTest{uuid.uuid4().hex}"


@windows_only
def test_tray_starts_reopens_on_request_and_stops() -> None:
    opened = threading.Event()
    name = unique_class()
    icon = TrayIcon(
        on_open=opened.set, on_quit=lambda: None, icon_path=default_icon_path(), window_class=name
    )
    assert icon.start() is True
    try:
        icon.notify("Prueba", "Aviso de prueba")  # no debe fallar
        assert signal_running(name) is True  # como si abrieras Erizo Coach otra vez
        assert opened.wait(5)
    finally:
        icon.stop()
    assert signal_running(name) is False  # ya no queda nada escuchando


@windows_only
def test_signal_running_without_an_open_copy() -> None:
    assert signal_running(unique_class()) is False


@windows_only
def test_tray_survives_a_missing_icon_file(tmp_path: Path) -> None:
    icon = TrayIcon(
        on_open=lambda: None,
        on_quit=lambda: None,
        icon_path=tmp_path / "no-existe.ico",
        window_class=unique_class(),
    )
    assert icon.start() is True  # usa el icono genérico de Windows
    icon.stop()


def test_development_icon_is_the_app_icon() -> None:
    path = default_icon_path()
    assert path is not None and path.name == "erizocoach.ico" and path.exists()


def test_tray_does_nothing_outside_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tray.sys, "platform", "linux")
    assert TrayIcon(on_open=lambda: None, on_quit=lambda: None).start() is False
    assert signal_running() is False


# --- Ventana: esconder y volver ------------------------------------------------------------


class FakeWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.scripts: list[str] = []

    def hide(self) -> None:
        self.calls.append("hide")

    def show(self) -> None:
        self.calls.append("show")

    def restore(self) -> None:
        self.calls.append("restore")

    def destroy(self) -> None:
        self.calls.append("destroy")

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


class FakeTray:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    def notify(self, title: str, text: str) -> None:
        self.notices.append((title, text))


class FakeApp:
    def __init__(self) -> None:
        self.presenter = UiPresenter(EventBus(), loaded_gamedata())


def make_window(*, with_tray: bool = True) -> tuple[DesktopWindow, FakeWindow, FakeTray]:
    window = DesktopWindow(FakeApp(), Settings(data_dir=Path(".")))  # type: ignore[arg-type]
    fake, icon = FakeWindow(), FakeTray()
    window._window = fake
    window._loaded = True
    window._tray = icon if with_tray else None  # type: ignore[assignment]
    return window, fake, icon


async def test_background_hides_and_stops_repainting() -> None:
    window, fake, icon = make_window()
    assert window.to_background() is True
    assert fake.calls == ["hide"]
    assert icon.notices == [BACKGROUND_NOTICE]
    await window._push(window._app.presenter.state)
    assert fake.scripts == []  # escondida no se repinta
    window.to_background()  # dos veces seguidas no hace nada raro
    assert fake.calls == ["hide"] and len(icon.notices) == 1  # el aviso, solo la primera vez


def test_bring_back_repaints_first_and_shows() -> None:
    window, fake, _ = make_window()
    window.to_background()
    window.bring_back()
    assert fake.calls == ["hide", "show"]  # no se toca el tamaño si no estaba minimizada
    assert len(fake.scripts) == 1 and "render(" in fake.scripts[0]


def test_bring_back_restores_a_minimized_window() -> None:
    window, fake, _ = make_window()
    window._on_minimized()
    window.bring_back()
    assert fake.calls == ["show", "restore"]
    assert fake.scripts == []  # estaba visible: ya estaba al día
    window._on_restored()
    window.bring_back()
    assert fake.calls == ["show", "restore", "show"]


def test_no_background_without_tray_icon() -> None:
    window, fake, _ = make_window(with_tray=False)
    assert window.to_background() is False  # no habría forma de volver a abrirla
    assert fake.calls == []


def test_quit_from_tray_closes_the_window() -> None:
    window, fake, _ = make_window()
    window.quit()
    assert fake.calls == ["destroy"]


def test_page_has_the_background_button() -> None:
    html = desktop.build_html("N")
    assert 'id="to-background"' in html and "Segundo plano" in html
    # Empieza escondido: solo aparece en la ventana de escritorio, cuando hay puente.
    assert '<button id="to-background" class="ghost" type="button" hidden' in html


def test_relaunch_opens_the_running_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from lolcoach import __main__ as entry

    shown: list[Any] = []

    class Busy:
        def __init__(self, _path: Path) -> None: ...
        def acquire(self) -> bool:
            return False

    monkeypatch.setattr(entry, "SingleInstanceLock", Busy)
    monkeypatch.setattr(tray, "signal_running", lambda: True)
    monkeypatch.setattr(entry, "_show_message", shown.append)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert entry.main([]) == 0
    assert shown == []  # sin el aviso de «ya está abierto»

    monkeypatch.setattr(tray, "signal_running", lambda: False)
    assert entry.main([]) == 1
    assert shown == ["Erizo Coach ya está abierto."]
