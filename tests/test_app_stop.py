"""Parar la app a petición (p. ej. «Actualizar» en modo navegador) es una salida limpia."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from erizocoach.app import Application, UiMode
from erizocoach.config import Settings


async def test_requested_stop_ends_run_cleanly(tmp_path: Path) -> None:
    app = Application(Settings(data_dir=tmp_path), ui_mode=UiMode.NONE)
    runner = asyncio.create_task(app.run())
    await asyncio.to_thread(app.started.wait, 5)
    app.quit()  # como hace UpdateService al lanzar el instalador
    await asyncio.wait_for(runner, 5)  # vuelve sin CancelledError
    assert runner.cancelled() is False and runner.exception() is None


async def test_cancellation_not_requested_still_propagates(tmp_path: Path) -> None:
    app = Application(Settings(data_dir=tmp_path), ui_mode=UiMode.NONE)
    runner = asyncio.create_task(app.run())
    await asyncio.to_thread(app.started.wait, 5)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner


def test_asyncio_run_returns_after_quit(tmp_path: Path) -> None:
    """Como en `main()` en modo navegador: `asyncio.run` no debe lanzar nada."""
    app = Application(Settings(data_dir=tmp_path), ui_mode=UiMode.NONE)

    async def scenario() -> None:
        runner = asyncio.create_task(app.run())
        await asyncio.to_thread(app.started.wait, 5)
        app.quit()
        await runner

    asyncio.run(scenario())
    app2 = Application(Settings(data_dir=tmp_path), ui_mode=UiMode.NONE)

    async def direct() -> None:
        asyncio.get_running_loop().call_later(0.2, app2.quit)
        await app2.run()

    asyncio.run(direct())


async def test_disabled_updates_are_explained_in_one_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="erizocoach.app")
    app = Application(Settings(data_dir=tmp_path), ui_mode=UiMode.NONE)
    runner = asyncio.create_task(app.run())
    await asyncio.to_thread(app.started.wait, 5)
    await asyncio.sleep(0)
    app.quit()
    await runner
    lines = [r.getMessage() for r in caplog.records if "versiones nuevas" in r.getMessage()]
    assert len(lines) == 1 and "desde el código" in lines[0]
