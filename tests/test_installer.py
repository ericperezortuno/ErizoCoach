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


installer = load("erizocoach_installer", ROOT / "packaging" / "installer.py")
builder = load("erizocoach_build_installer", ROOT / "scripts" / "build_installer.py")


def fake_app(folder: Path, marker: str = "v1") -> Path:
    (folder / "_internal").mkdir(parents=True)
    (folder / "Erizo Coach.exe").write_text(marker, encoding="utf-8")
    (folder / "erizocoach-consola.exe").write_text(marker, encoding="utf-8")
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
            "Erizo Coach.exe", "_internal/base_library.zip", "erizocoach-consola.exe",
        ]  # fmt: skip


def test_extract_installs_and_adds_the_uninstaller(tmp_path: Path) -> None:
    target = tmp_path / "Programs" / "Erizo Coach"
    installer.extract(payload_of(fake_app(tmp_path / "app"), tmp_path), target)
    assert (target / "Erizo Coach.exe").read_text(encoding="utf-8") == "v1"
    assert (target / "_internal" / "base_library.zip").exists()
    script = (target / "desinstalar.ps1").read_text(encoding="utf-8-sig")
    assert "Uninstall\\ErizoCoach" in script and "Erizo Coach.lnk" in script
    assert installer.LEGACY_UNINSTALL_KEY.endswith("\\LoLCoach")  # quitar la vieja, no esta
    assert "LOCALAPPDATA" not in script  # nunca toca tus datos (%LOCALAPPDATA%\ErizoCoach)


def test_update_replaces_the_old_version_without_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "Erizo Coach"
    installer.extract(payload_of(fake_app(tmp_path / "v1"), tmp_path, "v1.zip"), target)
    (target / "viejo.dll").write_text("x", encoding="utf-8")  # archivo que ya no existe en v2
    installer.extract(payload_of(fake_app(tmp_path / "v2", "v2"), tmp_path, "v2.zip"), target)
    assert (target / "Erizo Coach.exe").read_text(encoding="utf-8") == "v2"
    assert not (target / "viejo.dll").exists()
    assert not target.with_name("Erizo Coach.nuevo").exists()


def test_incomplete_or_tampered_payloads_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "Erizo Coach"
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("leeme.txt", "nada")
    with pytest.raises(installer.InstallError, match="incompleto"):
        installer.extract(empty, target)

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("Erizo Coach.exe", "app")
        archive.writestr("../../fuera.txt", "no")
    with pytest.raises(installer.InstallError, match="dañado"):
        installer.extract(evil, target)
    assert not (tmp_path / "fuera.txt").exists() and not target.exists()


def test_install_refuses_while_the_app_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installer, "app_running", lambda *_targets: True)
    places = installer.Places(target=tmp_path / "Erizo Coach", shortcuts=())
    payload = payload_of(fake_app(tmp_path / "app"), tmp_path)
    with pytest.raises(installer.InstallError, match="abierto"):
        installer.install(payload, places, "0.8.1", register=False)
    assert not places.target.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="accesos directos de Windows")
def test_install_creates_working_shortcuts(tmp_path: Path) -> None:
    places = installer.Places(target=tmp_path / "Erizo Coach", shortcuts=(tmp_path / "Inicio",))
    payload = payload_of(fake_app(tmp_path / "app"), tmp_path)
    exe = installer.install(payload, places, "0.8.1", register=False)
    assert exe == places.target / "Erizo Coach.exe"
    assert (tmp_path / "Inicio" / "Erizo Coach.lnk").stat().st_size > 0


def test_install_removes_the_app_with_the_old_name(tmp_path: Path) -> None:
    """Hasta la 0.18 se llamaba «LoL Coach»: se quitan su carpeta y sus accesos directos."""
    legacy = tmp_path / "Programs" / "LoL Coach"
    (legacy / "_internal").mkdir(parents=True)
    (legacy / "LoL Coach.exe").write_text("viejo", encoding="utf-8")
    start = tmp_path / "Inicio"
    start.mkdir()
    (start / "LoL Coach.lnk").write_text("acceso", encoding="utf-8")
    (start / "Otra app.lnk").write_text("acceso", encoding="utf-8")
    places = installer.Places(target=tmp_path / "Programs" / "Erizo Coach", shortcuts=(start,))
    installer.remove_legacy(places, register=False)
    assert not legacy.exists()
    assert not (start / "LoL Coach.lnk").exists()
    assert (start / "Otra app.lnk").exists()  # lo demás no se toca


def test_old_name_folder_without_the_app_is_not_deleted(tmp_path: Path) -> None:
    folder = tmp_path / "Programs" / "LoL Coach"
    folder.mkdir(parents=True)
    (folder / "mis cosas.txt").write_text("hola", encoding="utf-8")
    installer.remove_legacy(
        installer.Places(tmp_path / "Programs" / "Erizo Coach", ()), register=False
    )
    assert (folder / "mis cosas.txt").exists()


# --- WebView2: se descarga de Microsoft solo si falta, y nunca sin la firma de Microsoft ---


def test_download_only_over_https_and_with_a_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    with pytest.raises(installer.InstallError, match="HTTPS"):
        installer.download("http://go.microsoft.com/x", tmp_path / "a.exe")

    class FakeResponse(io.BytesIO):
        def geturl(self) -> str:
            return "https://msedge.sf.dl.delivery.mp.microsoft.com/setup.exe"

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_exc: object) -> None:
            self.close()

    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *_a, **_kw: FakeResponse(b"x" * 1000)
    )
    installer.download("https://go.microsoft.com/x", tmp_path / "ok.exe")
    assert (tmp_path / "ok.exe").stat().st_size == 1000
    with pytest.raises(installer.InstallError, match="más grande"):
        installer.download("https://go.microsoft.com/x", tmp_path / "big.exe", max_bytes=999)


@pytest.mark.skipif(sys.platform != "win32", reason="firmas de Windows")
def test_signature_check_accepts_microsoft_and_rejects_unsigned(tmp_path: Path) -> None:
    edge = next(
        (Path(p) for p in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ) if Path(p).exists()),
        None,
    )  # fmt: skip
    if edge is None:
        pytest.skip("Edge no está instalado")
    assert installer.signed_by_microsoft(edge)
    unsigned = tmp_path / "MicrosoftEdgeWebview2Setup.exe"
    unsigned.write_bytes(b"MZ no firmado")
    assert not installer.signed_by_microsoft(unsigned)


def test_unsigned_webview2_download_is_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[object] = []
    monkeypatch.setattr(installer, "download", lambda _url, dest: dest.write_bytes(b"MZ"))
    monkeypatch.setattr(installer, "signed_by_microsoft", lambda _path: False)
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: ran.append(a))
    with pytest.raises(installer.InstallError, match="firma"):
        installer.install_webview2(tmp_path)
    assert ran == []


def test_webview2_download_failure_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(_url: str, _dest: Path) -> None:
        raise OSError("sin conexión")

    monkeypatch.setattr(installer, "download", offline)
    with pytest.raises(installer.InstallError, match="descargar"):
        installer.install_webview2(tmp_path)


def run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, has_webview2: bool, fails: bool):  # type: ignore[no-untyped-def]
    """Ejecuta el instalador «con ventanas» respondiendo Sí a todo; devuelve qué pasó."""
    seen: dict[str, object] = {"messages": [], "webview2": 0, "opened": []}
    monkeypatch.setattr(installer, "bundled", lambda name: tmp_path / name)
    (tmp_path / installer.VERSION_FILE).write_text("9.9.9", encoding="utf-8")
    monkeypatch.setattr(installer, "default_places", lambda: None)
    monkeypatch.setattr(installer, "install", lambda *_a, **_kw: tmp_path / "Erizo Coach.exe")
    monkeypatch.setattr(installer, "webview2_installed", lambda: has_webview2)

    def fake_webview2(_work: Path) -> None:
        seen["webview2"] += 1  # type: ignore[operator]
        if fails:
            raise installer.InstallError("sin conexión")

    def fake_message(text: str, _flags: int = 0) -> int:
        seen["messages"].append(text)  # type: ignore[attr-defined]
        return installer.IDYES

    monkeypatch.setattr(installer, "install_webview2", fake_webview2)
    monkeypatch.setattr(installer, "message", fake_message)
    monkeypatch.setattr(installer, "with_progress", lambda _text, work: work(lambda _t: None))
    monkeypatch.setattr(
        installer.subprocess, "Popen", lambda args, **_kw: seen["opened"].append(args)
    )
    assert installer.main([]) == 0
    return seen


def test_installer_skips_webview2_when_the_pc_has_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = run_main(monkeypatch, tmp_path, has_webview2=True, fails=False)
    assert seen["webview2"] == 0
    assert not any("WebView2" in m for m in seen["messages"])  # type: ignore[attr-defined]
    assert seen["opened"]  # se abre la app al final


def test_installer_installs_webview2_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = run_main(monkeypatch, tmp_path, has_webview2=False, fails=False)
    assert seen["webview2"] == 1
    first, last = seen["messages"][0], seen["messages"][-1]  # type: ignore[index]
    assert "WebView2" in first and "internet" in first
    assert "No se ha podido" not in last


def test_installer_still_works_if_webview2_cannot_be_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = run_main(monkeypatch, tmp_path, has_webview2=False, fails=True)
    last = seen["messages"][-1]  # type: ignore[index]
    assert "No se ha podido instalar WebView2 (sin conexión)" in last and "Edge" in last
    assert seen["opened"]  # Erizo Coach queda instalado y se puede abrir


def run_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    *,
    exited: bool = True,
    has_webview2: bool = True,
    install_error: Exception | None = None,
):  # type: ignore[no-untyped-def]
    """Ejecuta el instalador en modo ``--actualizar``; devuelve (código, qué pasó)."""
    seen: dict[str, list] = {"messages": [], "waits": [], "installs": [], "opened": []}
    monkeypatch.setattr(installer, "bundled", lambda name: tmp_path / name)
    (tmp_path / installer.VERSION_FILE).write_text("9.9.9", encoding="utf-8")
    places = installer.Places(tmp_path / "Programs" / "Erizo Coach", ())  # no es la cwd
    monkeypatch.setattr(installer, "default_places", lambda: places)

    def fake_install(*_a: object, **_kw: object) -> Path:
        seen["installs"].append(1)
        if install_error:
            raise install_error
        return tmp_path / "Erizo Coach.exe"

    def fake_wait(pid: int, timeout: float) -> bool:
        seen["waits"].append((pid, timeout))
        return exited

    def fake_message(text: str, flags: int = 0) -> int:
        seen["messages"].append((text, flags))
        return installer.IDYES

    def fake_webview2(_work: Path) -> None:
        raise installer.InstallError("sin conexión")

    monkeypatch.setattr(installer, "install", fake_install)
    monkeypatch.setattr(installer, "wait_for_exit", fake_wait)
    monkeypatch.setattr(installer, "message", fake_message)
    monkeypatch.setattr(installer, "webview2_installed", lambda: has_webview2)
    monkeypatch.setattr(installer, "install_webview2", fake_webview2)
    monkeypatch.setattr(installer, "with_progress", lambda _text, work: work(lambda _t: None))
    monkeypatch.setattr(
        installer.subprocess, "Popen", lambda args, **_kw: seen["opened"].append(args)
    )
    return installer.main(args), seen


def test_update_mode_installs_without_asking_and_reopens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, seen = run_update(monkeypatch, tmp_path, ["--actualizar", "1234"])
    assert code == 0
    assert seen["installs"] and seen["messages"] == []  # ni pregunta ni mensaje final
    assert seen["opened"] == [["explorer.exe", str(tmp_path / "Erizo Coach.exe")]]


def test_update_mode_waits_for_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, seen = run_update(monkeypatch, tmp_path, ["--actualizar", "1234"])
    assert seen["waits"] == [(1234, 30)]


def test_update_mode_app_still_open_warns_and_does_not_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, seen = run_update(monkeypatch, tmp_path, ["--actualizar", "1234"], exited=False)
    assert code == 1
    assert not seen["installs"] and not seen["opened"]
    assert len(seen["messages"]) == 1 and "no se ha cerrado a tiempo" in seen["messages"][0][0]


@pytest.mark.parametrize(
    "args",
    [["--actualizar", "abc"], ["--actualizar", "0"], ["--actualizar"], ["--actualizar", "-5"],
     ["--actualizar", "1", "2"]],
)  # fmt: skip
def test_update_mode_bad_pid_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str]
) -> None:
    code, seen = run_update(monkeypatch, tmp_path, args)
    assert code == 1
    assert not seen["installs"] and not seen["waits"] and not seen["opened"]
    assert len(seen["messages"]) == 1


def test_update_mode_webview2_problem_is_silent_and_still_reopens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, seen = run_update(monkeypatch, tmp_path, ["--actualizar", "7"], has_webview2=False)
    assert code == 0 and seen["messages"] == [] and seen["opened"]


def test_update_mode_install_error_warns_and_does_not_reopen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    err = installer.InstallError("Erizo Coach está abierto")
    code, seen = run_update(monkeypatch, tmp_path, ["--actualizar", "7"], install_error=err)
    assert code == 1 and not seen["opened"]
    assert seen["messages"] == [
        ("Erizo Coach está abierto", installer.MB_OK | installer.MB_ICONWARN)
    ]


def test_update_mode_unexpected_error_is_shown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, seen = run_update(
        monkeypatch, tmp_path, ["--actualizar", "7"], install_error=PermissionError("denegado")
    )
    assert code == 1 and not seen["opened"]
    assert "denegado" in seen["messages"][0][0]


def test_wait_for_exit_false_while_alive_true_once_gone() -> None:
    import subprocess

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert installer.wait_for_exit(child.pid, 0.2) is False
        child.kill()
        child.wait()
        assert installer.wait_for_exit(child.pid, 5) is True
    finally:
        child.kill()


def test_exe_version_info_matches_the_app_version() -> None:
    """Lo que Windows enseña en Propiedades > Detalles es la versión de verdad."""
    sys.path.insert(0, str(ROOT / "src"))
    from erizocoach import __version__

    text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    numbers = ", ".join(__version__.split(".")) + ", 0"
    assert text.count(f"({numbers})") == 2
    assert text.count(f"'{__version__}'") == 2


# --- Carpeta de la app ocupada (fallo real del 17/11: el instalador heredó la carpeta) -------


def busy_folder(folder: Path):  # type: ignore[no-untyped-def]
    """Un proceso con ``folder`` como carpeta de trabajo: Windows no deja moverla ni borrarla."""
    import subprocess

    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=folder)


@pytest.mark.skipif(sys.platform != "win32", reason="el bloqueo de carpetas es de Windows")
def test_folder_in_use_keeps_the_old_version_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(installer, "RENAME_RETRY_SECONDS", 0.5)
    target = tmp_path / "Erizo Coach"
    installer.extract(payload_of(fake_app(tmp_path / "v1"), tmp_path, "v1.zip"), target)
    holder = busy_folder(target)
    try:
        with pytest.raises(installer.InstallError, match="en uso"):
            installer.extract(
                payload_of(fake_app(tmp_path / "v2", "v2"), tmp_path, "v2.zip"), target
            )
        assert (target / "Erizo Coach.exe").read_text(encoding="utf-8") == "v1"  # intacta
        assert (target / "_internal" / "base_library.zip").exists()
        assert not target.with_name("Erizo Coach.nuevo").exists()
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="el bloqueo de carpetas es de Windows")
def test_folder_busy_for_a_moment_is_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import threading

    monkeypatch.setattr(installer, "RENAME_RETRY_SECONDS", 10)
    target = tmp_path / "Erizo Coach"
    installer.extract(payload_of(fake_app(tmp_path / "v1"), tmp_path, "v1.zip"), target)
    holder = busy_folder(target)
    threading.Timer(1.0, holder.kill).start()  # p. ej., WebView2 tardando en cerrarse
    try:
        installer.extract(payload_of(fake_app(tmp_path / "v2", "v2"), tmp_path, "v2.zip"), target)
        assert (target / "Erizo Coach.exe").read_text(encoding="utf-8") == "v2"
        assert not target.with_name("Erizo Coach.viejo").exists()
    finally:
        holder.kill()
        holder.wait()


def test_update_mode_started_inside_the_app_folder_relaunches_from_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La app (0.23/0.24.0) lo abre con su carpeta como carpeta de trabajo: se relanza fuera."""
    target = tmp_path / "Programs" / "Erizo Coach"
    (target / "_internal").mkdir(parents=True)
    monkeypatch.setattr(installer, "default_places", lambda: installer.Places(target, ()))
    monkeypatch.setattr(installer.Path, "cwd", classmethod(lambda cls: target / "_internal"))
    launched: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        installer.subprocess, "Popen", lambda args, **kw: launched.append((args, kw))
    )
    monkeypatch.setattr(installer, "wait_for_exit", lambda *_a: pytest.fail("no debe esperar"))
    monkeypatch.setattr(installer, "install_all", lambda *_a: pytest.fail("no debe instalar"))
    monkeypatch.setattr(installer, "bundled", lambda _n: _version_file(tmp_path))
    assert installer.main(["--actualizar", "123"]) == 0
    ((args, kwargs),) = launched
    assert args == [sys.executable, "--actualizar", "123"]
    assert Path(str(kwargs["cwd"])) == Path(installer.tempfile.gettempdir())
    flags = int(kwargs["creationflags"])  # type: ignore[call-overload]
    assert flags & 0x00000008 and flags & 0x00000200  # independiente de quien lo abrió


def _version_file(tmp_path: Path) -> Path:
    path = tmp_path / "version.txt"
    path.write_text("0.24.1", encoding="utf-8")
    return path
