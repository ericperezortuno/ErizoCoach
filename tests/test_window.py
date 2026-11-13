"""Posición de la ventana en tus monitores."""

from __future__ import annotations

from pathlib import Path

import pytest

from lolcoach.ui.web import window
from lolcoach.ui.web.window import AppWindow, Monitor, window_geometry

PRIMARY = Monitor(0, 0, 1920, 1032, True)


def test_opens_centered_on_the_secondary_with_the_chosen_size() -> None:
    # Tu configuración: el segundo monitor está a la izquierda del principal
    left = Monitor(-1920, 0, 1920, 1032, False)
    assert window_geometry([PRIMARY, left]) == (-1488, 166, 1056, 700)


def test_secondary_on_the_right() -> None:
    right = Monitor(1920, 0, 2560, 1400, False)
    assert window_geometry([right, PRIMARY]) == (2672, 350, 1056, 700)


def test_single_monitor_centers_on_the_primary() -> None:
    assert window_geometry([PRIMARY]) == (432, 166, 1056, 700)


def test_prefer_primary() -> None:
    left = Monitor(-1920, 0, 1920, 1032, False)
    assert window_geometry([PRIMARY, left], prefer_secondary=False) == (432, 166, 1056, 700)


def test_small_screens_never_get_a_window_bigger_than_them() -> None:
    small = Monitor(0, 0, 1024, 600, True)
    assert window_geometry([small]) == (0, 0, 1024, 600)


def test_no_monitor_information() -> None:
    assert window_geometry([]) is None


def test_leftover_windows_are_closed_but_never_your_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "window"

    class FakeProcess:
        def __init__(self, name: str, args: list[str]) -> None:
            self.info = {"name": name}
            self.args = args
            self.terminated = False

        def cmdline(self) -> list[str]:
            return self.args

        def terminate(self) -> None:
            self.terminated = True

    ours = FakeProcess("msedge.exe", ["msedge.exe", f"--user-data-dir={profile}", "--app=x"])
    your_browser = FakeProcess("msedge.exe", ["msedge.exe", "--profile-directory=Default"])
    other_app = FakeProcess("chrome.exe", ["chrome.exe", f"--user-data-dir={profile}"])
    processes = [ours, your_browser, other_app]
    monkeypatch.setattr(window.psutil, "process_iter", lambda attrs: iter(processes))

    assert AppWindow(profile).close_leftovers() == 1
    assert ours.terminated and not your_browser.terminated and not other_app.terminated
