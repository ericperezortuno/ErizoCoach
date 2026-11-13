"""Ventana de escritorio: página incrustada, puente con la app y elección del modo."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
from pathlib import Path

import pytest

from lolcoach.__main__ import choose_ui_mode
from lolcoach.app import UiMode
from lolcoach.bus import EventBus
from lolcoach.config import Settings
from lolcoach.ui import desktop
from lolcoach.ui.desktop import DesktopBridge, build_html, render_script
from lolcoach.ui.web.presenter import UiPresenter

from .gamedata_fixture import loaded_gamedata

PNG = b"\x89PNG\r\n\x1a\nfalso"


class FakeIcons:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def get(self, kind: str, ident: int) -> bytes | None:
        self.calls.append((kind, ident))
        return PNG if ident == 3157 else None


@pytest.fixture
def running_loop():
    """Un bucle en otro hilo, como el de la app cuando la ventana ocupa el hilo principal."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


# --- Página -------------------------------------------------------------------------


def test_html_is_self_contained_with_strict_policy() -> None:
    html = build_html("NONCE123")
    assert 'data-mode="desktop"' in html
    assert 'src="/static' not in html and 'href="/static' not in html  # todo incrustado
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert csp is not None
    policy = csp.group(1)
    assert "default-src 'none'" in policy and "connect-src 'none'" in policy
    assert "script-src 'nonce-NONCE123'" in policy and "img-src data:" in policy
    assert html.count('nonce="NONCE123"') == 2  # el estilo y el código, nada más


def test_each_launch_uses_a_new_nonce() -> None:
    first = re.search(r"nonce-([\w-]+)", build_html())
    second = re.search(r"nonce-([\w-]+)", build_html())
    assert first and second and first.group(1) != second.group(1)


def test_render_script_is_safe_json() -> None:
    script = render_script({"phaseLabel": "</script><b>hola</b>   ñ"})
    payload = script.removeprefix("window.lolcoach && window.lolcoach.render(").removesuffix(")")
    assert json.loads(payload)["phaseLabel"].startswith("</script>")
    assert "ñ" not in script and " " not in script  # solo ASCII


# --- Puente con la app -----------------------------------------------------------------


def test_bridge_returns_state_and_icons(running_loop: asyncio.AbstractEventLoop) -> None:
    presenter = UiPresenter(EventBus(), loaded_gamedata())
    icons = FakeIcons()
    bridge = DesktopBridge(presenter, icons, running_loop)  # type: ignore[arg-type]
    assert bridge.get_state()["phaseLabel"] == "Esperando al cliente de LoL"
    src = bridge.icon("item", "3157")
    assert src == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert bridge.icon("item", 1) is None  # no existe


@pytest.mark.parametrize(
    ("kind", "ident"),
    [("file", 1), ("item", "../../secreto"), ("item", -5), ("item", 10**9), ("champion", None)],
)
def test_bridge_rejects_anything_else(
    running_loop: asyncio.AbstractEventLoop, kind: object, ident: object
) -> None:
    icons = FakeIcons()
    bridge = DesktopBridge(UiPresenter(EventBus(), loaded_gamedata()), icons, running_loop)  # type: ignore[arg-type]
    assert bridge.icon(kind, ident) is None
    assert icons.calls == []  # ni siquiera se intenta


def test_bridge_exposes_only_two_methods() -> None:
    public = [name for name in dir(DesktopBridge) if not name.startswith("_")]
    assert public == ["get_state", "icon"]


# --- Elección del modo ------------------------------------------------------------------


def settings(**changes: object) -> Settings:
    return Settings(data_dir=Path("."), **changes)  # type: ignore[arg-type]


def test_ui_mode_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "desktop_available", lambda: True)
    assert choose_ui_mode(settings(), demo=False, browser=False) == UiMode.DESKTOP
    assert choose_ui_mode(settings(), demo=False, browser=True) == UiMode.BROWSER
    assert (
        choose_ui_mode(settings(window_mode="navegador"), demo=False, browser=False)
        == UiMode.BROWSER
    )
    assert choose_ui_mode(settings(window=False), demo=False, browser=False) == UiMode.NONE
    assert choose_ui_mode(settings(window=False), demo=True, browser=False) == UiMode.SERVER_ONLY


def test_falls_back_to_edge_without_webview2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "desktop_available", lambda: False)
    assert choose_ui_mode(settings(), demo=False, browser=False) == UiMode.BROWSER
