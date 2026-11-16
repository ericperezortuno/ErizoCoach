"""Intro al abrir League of Legends: página, secuencia en Windows y cuándo sale."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from erizocoach.config import Settings, load_settings
from erizocoach.ui import intro
from erizocoach.ui.desktop import DesktopWindow
from erizocoach.ui.intro import LaunchIntro, build_intro_html

PRIMARY = (0, 0, 2400, 1350)  # monitor al 125 %: en píxeles lógicos, 1920x1080


class FakeEvent:
    def __init__(self, fires: bool) -> None:
        self.fires = fires

    def wait(self, _timeout: float) -> bool:
        return self.fires


class FakeWindow:
    def __init__(self, log: list[Any], *, loads: bool = True) -> None:
        self.log = log
        self.events = SimpleNamespace(loaded=FakeEvent(loads))

    def move(self, x: int, y: int) -> None:
        self.log.append(("move", x, y))

    def resize(self, width: int, height: int) -> None:
        self.log.append(("resize", width, height))

    def show(self) -> None:
        self.log.append("show")

    def evaluate_js(self, script: str) -> None:
        self.log.append(("js", script))

    def destroy(self) -> None:
        self.log.append("destroy")


class FakeScreen:
    def __init__(self, log: list[Any], rect: tuple[int, int, int, int] | None = PRIMARY) -> None:
        self.log = log
        self.rect = rect
        self.alphas: list[int] = []

    def primary_rect(self) -> tuple[int, int, int, int] | None:
        return self.rect

    def find_window(self, title: str) -> int:
        self.log.append(("find", title))
        return 77

    def scale(self, _hwnd: int) -> float:
        return 1.25

    def make_overlay(self, hwnd: int) -> None:
        self.log.append(("overlay", hwnd))

    def set_alpha(self, _hwnd: int, alpha: int) -> None:
        self.alphas.append(alpha)

    def foreground(self) -> int:
        return 5  # la ventana de Erizo Coach, donde acabas de hacer clic

    def set_foreground(self, hwnd: int) -> None:
        self.log.append(("foreground", hwnd))


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def player(log: list[Any], *, loads: bool = True, rect: Any = PRIMARY):  # type: ignore[no-untyped-def]
    created: list[dict[str, Any]] = []

    def create_window(title: str, **options: Any) -> FakeWindow:
        created.append({"title": title, **options})
        log.append("create")
        return FakeWindow(log, loads=loads)

    clock = Clock()
    screen = FakeScreen(log, rect)
    launch_intro = LaunchIntro(
        create_window=create_window, screen=screen, clock=clock, sleep=clock.sleep
    )
    return launch_intro, created, screen, clock


# --- Página -------------------------------------------------------------------------


def test_intro_page_is_self_contained_with_strict_policy() -> None:
    html = build_intro_html("NONCE9")
    assert "/static/" not in html  # estilos, código y erizo incrustados
    assert html.count("data:image/png;base64,iVBORw0KGgo") == 1
    policy = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert policy is not None
    assert "default-src 'none'" in policy.group(1) and "connect-src 'none'" in policy.group(1)
    assert html.count('nonce="NONCE9"') == 2
    assert "window.erizoIntro = { play }" in html and 'id="stage"' in html


def test_page_timings_match_the_window() -> None:
    """El CSS y el JS se apagan cuando la ventana empieza a desvanecerse (ui/intro.py)."""
    html = build_intro_html("N")
    out = float(re.search(r"--out: ([\d.]+)s", html).group(1))  # type: ignore[union-attr]
    assert re.search(rf"OUT_MS = {round(out * 1000)}\b", html)
    assert out <= intro.DURATION - intro.FADE_OUT  # se ve el final antes de desvanecerse
    end = int(re.search(r"END_MS = (\d+)", html).group(1))  # type: ignore[union-attr]
    assert end / 1000 >= intro.DURATION  # las chispas no se cortan antes de cerrar


# --- Secuencia en Windows --------------------------------------------------------------


def test_intro_covers_the_primary_monitor_without_taking_focus() -> None:
    log: list[Any] = []
    launch_intro, created, screen, clock = player(log)
    launch_intro._run()
    options = created[0]
    assert options["hidden"] and options["frameless"] and options["on_top"]
    assert options["focus"] is False and "js_api" not in options  # la página no pide nada
    steps = [entry if isinstance(entry, str) else entry[0] for entry in log]
    assert steps == [
        "create", "find", "overlay", "move", "resize", "show", "foreground", "js", "destroy",
    ]  # fmt: skip
    assert ("find", options["title"]) in log
    assert ("move", 0, 0) in log and ("resize", 1920, 1080) in log  # 125 %: lógicos
    assert ("foreground", 5) in log  # el teclado vuelve a donde estaba
    assert ("js", intro.PLAY_SCRIPT) in log
    # Aparece con fundido, se queda entera y se desvanece del todo antes de cerrarse.
    peak = screen.alphas.index(255)
    assert screen.alphas[:peak] == sorted(screen.alphas[:peak]) and screen.alphas[0] < 64
    assert screen.alphas[peak:] == sorted(screen.alphas[peak:], reverse=True)
    assert screen.alphas[-1] == 0
    assert abs(clock.now - 100.0 - intro.DURATION) < 0.05  # dura lo que dice


def test_page_that_never_loads_is_closed_without_showing() -> None:
    log: list[Any] = []
    launch_intro, _, screen, _ = player(log, loads=False)
    launch_intro._run()
    assert "show" not in log and "destroy" in log and screen.alphas == []


def test_without_a_monitor_nothing_is_created() -> None:
    log: list[Any] = []
    launch_intro, created, _, _ = player(log, rect=None)
    launch_intro._run()
    assert created == [] and log == []


def test_a_failure_never_reaches_the_app() -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("WebView2 se ha ido")

    launch_intro = LaunchIntro(create_window=broken, screen=FakeScreen([]))
    launch_intro._run()  # solo se registra
    assert not launch_intro.playing


def test_only_one_intro_at_a_time() -> None:
    release = threading.Event()
    log: list[Any] = []

    def slow_create(title: str, **_options: Any) -> FakeWindow:
        release.wait(5)
        return FakeWindow(log)

    clock = Clock()
    launch_intro = LaunchIntro(
        create_window=slow_create, screen=FakeScreen(log), clock=clock, sleep=clock.sleep
    )
    assert launch_intro.play()
    assert not launch_intro.play()  # otro clic mientras se ve: no se duplica
    release.set()
    deadline = time.monotonic() + 5
    while launch_intro.playing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not launch_intro.playing and "destroy" in log
    assert launch_intro.play()  # terminada, la siguiente vez vuelve a salir
    deadline = time.monotonic() + 5
    while launch_intro.playing and time.monotonic() < deadline:
        time.sleep(0.01)


# --- Cuándo sale -----------------------------------------------------------------------


class CountingIntro:
    def __init__(self) -> None:
        self.plays = 0

    def play(self) -> bool:
        self.plays += 1
        return True


def desktop_window(tmp_path: Path, **settings: Any) -> tuple[DesktopWindow, CountingIntro]:
    window = DesktopWindow(SimpleNamespace(), Settings(data_dir=tmp_path, **settings))  # type: ignore[arg-type]
    counting = CountingIntro()
    window._intro = counting  # type: ignore[assignment]
    return window, counting


def test_intro_plays_once_when_the_client_starts_opening(tmp_path: Path) -> None:
    window, counting = desktop_window(tmp_path)
    for launch in ("idle", "starting", "starting", "starting"):  # repintados mientras abre
        window._intro_on_launch({"launch": launch, "connected": False})
    assert counting.plays == 1
    window._intro_on_launch({"launch": "idle", "connected": False})  # se dejó de esperar
    window._intro_on_launch({"launch": "starting", "connected": False})  # nuevo clic
    assert counting.plays == 2


def test_no_intro_when_not_found_or_turned_off(tmp_path: Path) -> None:
    window, counting = desktop_window(tmp_path)
    for launch in ("notFound", "failed", "idle"):
        window._intro_on_launch({"launch": launch, "connected": False})
    assert counting.plays == 0
    window, counting = desktop_window(tmp_path, launch_intro=False)
    window._intro_on_launch({"launch": "starting", "connected": False})
    assert counting.plays == 0


def test_launch_intro_setting(tmp_path: Path) -> None:
    assert load_settings(data_dir=tmp_path)[0].launch_intro is True
    (tmp_path / "config.toml").write_text("launch_intro = false\n", encoding="utf-8")
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.launch_intro is False and warnings == []
