# Botón «Actualizar» (Releases de GitHub) — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que la app instalada vea las versiones publicadas como releases en GitHub y se actualice con un clic, solo con instaladores firmados por el autor.

**Architecture:** un paquete nuevo `erizocoach/update` (manifiesto firmado con Ed25519, descargas con lista de dominios y un `UpdateService` en el bus de eventos) que el presentador muestra como una tarjeta con botón. El instalador gana un modo `--actualizar <pid>`, y un script de desarrollo, `scripts/publicar_version.py`, firma y crea la release por la API de GitHub con un token *fine-grained* guardado en el Administrador de credenciales.

**Tech Stack:** Python 3.13, asyncio + aiohttp, `cryptography` (Ed25519), keyring, pywebview/JS, PyInstaller, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-boton-actualizar-design.md`

## Global Constraints

- Repo de las releases: `ericperezortuno/ErizoCoach`. Base: `https://github.com/ericperezortuno/ErizoCoach/releases`.
- Archivos de cada release: `erizocoach-version.json`, `erizocoach-version.json.sig` (firma Ed25519 en base64, una línea) y `Instalar-Erizo-Coach-X.Y.Z.exe`. Sin espacios: GitHub los cambia por puntos.
- Manifiesto (JSON UTF-8): `{"version", "installer", "size", "sha256", "notes", "published"}`.
- Descargas: solo `https`; dominios `github.com` y `*.githubusercontent.com`; redirecciones seguidas a mano (máx. 5), comprobando el dominio en cada salto. Manifiesto ≤ 64 KB, firma ≤ 1 KB, instalador ≤ 200 MB y exactamente `size` bytes con ese `sha256`.
- Comprobación: solo si `getattr(sys, "frozen", False)`, con `settings.update_check` activado y con `PUBLIC_KEY` no vacía. Primera a los 30 s, luego cada 6 h.
- Fases seguras para actualizar: cliente desconectado, `None`, `Lobby` o `EndOfGame`. Las mismas en la ventana y en el servicio.
- Solo versiones estrictamente mayores que `__version__` (comparación numérica X.Y.Z).
- Keyring del publicador: servicio `ErizoCoach-publicar`, cuentas `github_token` y `clave_firma` (privada Ed25519, 32 bytes en base64). Nunca en el repo, el `.exe` ni los registros (`REDACTOR.add_secret`).
- Commits con la identidad del repo (correo noreply de GitHub). Textos para el usuario en español.

## Review Focus

1. **Descarga cortada a medias** (sin red, conexión que se cae): no se lanza nada, se borra el archivo parcial y el botón vuelve con el error. Test en la tarea 3.
2. **Empieza la selección de campeón mientras se descarga**: justo antes de lanzar el instalador se vuelve a mirar la fase y, si no es segura, no se lanza ni se cierra la app. Test en la tarea 3.
3. **La última release no trae manifiesto (404) o es más antigua**: no se muestra ningún aviso ni error, solo una línea de depuración. Test en la tarea 3.
4. **App ejecutada desde el código** (`uv run`, no congelada): nunca comprueba ni enseña el botón. Test en la tarea 3.
5. **La app no se cierra a tiempo** al actualizar: el instalador avisa y no instala encima de la app abierta. Test en la tarea 5.

---

### Task 1: Manifiesto firmado

**Files:**
- Create: `src/erizocoach/update/__init__.py`, `src/erizocoach/update/manifest.py`, `src/erizocoach/update/signing_key.py`
- Modify: `pyproject.toml` (dependencia `cryptography>=44`), `uv.lock` (`uv lock`)
- Test: `tests/test_update_manifest.py`

**Interfaces:**
- Produces:
  - `class UpdateError(Exception)`.
  - `@dataclass(frozen=True, slots=True) class Manifest: version: str; installer: str; size: int; sha256: str; notes: str; published: str`.
  - `parse_version(text: str) -> tuple[int, int, int]` (lanza `UpdateError`).
  - `encode_manifest(m: Manifest) -> bytes` (JSON con claves ordenadas, UTF-8).
  - `sign(data: bytes, private_key: bytes) -> bytes` (devuelve la firma en base64 ASCII).
  - `verify_manifest(data: bytes, signature: bytes, public_key: bytes) -> Manifest` (verifica y después valida los campos; lanza `UpdateError`).
  - `INSTALLER_NAME = re.compile(r"^Instalar-Erizo-Coach-\d{1,3}\.\d{1,3}\.\d{1,3}\.exe$")`.
  - `signing_key.PUBLIC_KEY: bytes = b""` (32 bytes cuando exista la clave real; tarea 7).

- [ ] **Step 1: Tests que fallan.** Con un par de claves generado en el test (`Ed25519PrivateKey.generate()`):
  - `test_signed_manifest_round_trips`: `verify_manifest(encode_manifest(m), sign(...), pub) == m`.
  - `test_tampered_manifest_is_rejected`: cambiar un byte → `UpdateError`.
  - `test_other_key_is_rejected`.
  - `test_bad_fields_are_rejected`, parametrizado: `installer` = `"..\\malo.exe"`, `"Instalar Erizo Coach 1.0.0.exe"` y `"x.exe"`; `sha256` que no sean 64 hexadecimales; `size` ≤ 0 o > 200_000_000; versión `"1.2"`; JSON que no es un objeto. Todos lanzan `UpdateError`.
  - `test_versions_compare_numerically`: `parse_version("0.10.0") > parse_version("0.9.9")`.
- [ ] **Step 2:** `uv run --frozen pytest tests/test_update_manifest.py -q` → falla al importar.
- [ ] **Step 3:** Implementar `manifest.py` con `cryptography.hazmat.primitives.asymmetric.ed25519` (`from_private_bytes`, `from_public_bytes`, `InvalidSignature` → `UpdateError`). Añadir la dependencia y ejecutar `uv lock` y `uv sync --frozen`.
- [ ] **Step 4:** el test pasa; `ruff check` limpio.
- [ ] **Step 5:** commit «Actualizar: manifiesto firmado con Ed25519».

### Task 2: Descargas con lista de dominios

**Files:**
- Create: `src/erizocoach/update/download.py`
- Test: `tests/test_update_download.py` (servidor aiohttp local; los dominios se inyectan para poder usar 127.0.0.1)

**Interfaces:**
- Produces:
  - `RELEASES = "https://github.com/ericperezortuno/ErizoCoach/releases"`, `MANIFEST_URL = f"{RELEASES}/latest/download/erizocoach-version.json"`, `SIGNATURE_URL = MANIFEST_URL + ".sig"`, `installer_url(m: Manifest) -> str` (`f"{RELEASES}/download/v{m.version}/{m.installer}"`).
  - `allowed_url(url: str, hosts: tuple[str, ...] = ALLOWED_HOSTS, *, schemes: tuple[str, ...] = ("https",)) -> bool`; `ALLOWED_HOSTS = ("github.com", ".githubusercontent.com")` (un punto delante = subdominios).
  - `class Downloader(session_factory=aiohttp.ClientSession, hosts=ALLOWED_HOSTS, schemes=("https",))` con `async fetch(url: str, limit: int) -> bytes | None` (404 → `None`; otros fallos → `UpdateError`) y `async to_file(url: str, path: Path, size: int, sha256: str, progress: Callable[[int], Awaitable[None]]) -> None` (descarga por trozos, corta al pasar de `size`, comprueba el SHA-256 y borra el archivo si algo falla; `progress` recibe el porcentaje de 0 a 100).

- [ ] **Step 1: Tests que fallan.**
  - `test_allowed_urls`: aceptadas `https://github.com/...` y `https://objects.githubusercontent.com/...`; rechazadas `http://github.com`, `https://github.com.evil.io`, `https://evilgithub.com` y `https://githubusercontent.com.evil.io`.
  - `test_redirect_to_other_host_is_refused`: el servidor falso redirige a un dominio fuera de la lista → `UpdateError` sin seguir.
  - `test_fetch_returns_none_on_404`.
  - `test_fetch_respects_limit`: respuesta más grande que `limit` → `UpdateError`.
  - `test_download_checks_size_and_hash`: hash correcto → archivo escrito y `progress` llega a 100; hash distinto o cuerpo más largo → `UpdateError` y el archivo no existe.
  - `test_download_cut_halfway_leaves_no_file`: el servidor cierra a mitad → `UpdateError` y no queda archivo (Review Focus 1).
- [ ] **Step 2:** comprobar que fallan.
- [ ] **Step 3:** implementar con `allow_redirects=False`, siguiendo a mano los `Location` 301/302/303/307/308 (máx. 5) y comprobando `allowed_url` en cada salto.
- [ ] **Step 4:** los tests pasan.
- [ ] **Step 5:** commit «Actualizar: descargas solo de GitHub, con límites y hash».

### Task 3: Servicio de actualización, eventos y ajuste

**Files:**
- Create: `src/erizocoach/update/service.py`
- Modify: `src/erizocoach/events.py`, `src/erizocoach/config.py` (`update_check: bool = True`, leído con `_bool` y documentado en el docstring), `src/erizocoach/app.py`
- Test: `tests/test_update_service.py`

**Interfaces:**
- Consumes: Task 1 (`verify_manifest`, `parse_version`, `PUBLIC_KEY`) y Task 2 (`Downloader`, URLs).
- Produces:
  - En `events.py`: `class UpdateState(StrEnum): AVAILABLE="available", DOWNLOADING="downloading", INSTALLING="installing", FAILED="failed"`; `@dataclass(frozen=True, slots=True) class UpdateStatus: version: str; notes: str; state: UpdateState; progress: int = 0; error: str = ""`; `@dataclass(frozen=True, slots=True) class UpdateRequested`.
  - En `service.py`: `SAFE_PHASES = frozenset({GameflowPhase.NONE, GameflowPhase.LOBBY, GameflowPhase.END_OF_GAME})` y la clase `UpdateService(bus, *, current_version: str, public_key: bytes, downloader: Downloader, download_dir: Path, launch_installer: Callable[[Path], None], quit_app: Callable[[], None], first_delay: float = 30.0, interval: float = 6 * 3600)`, con `async run()`, `async check() -> Manifest | None`, `safe` (property: desconectado o fase en `SAFE_PHASES`; se sigue con `ClientConnected`, `ClientDisconnected` y `GameflowPhaseChanged`) y el manejador de `UpdateRequested`.
  - `should_check(settings: Settings, public_key: bytes) -> bool` (congelado, `update_check` y clave no vacía).
  - `launch_installer_detached(path: Path) -> None`: `subprocess.Popen([str(path), "--actualizar", str(os.getpid())], creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`.
  - `Application.quit: Callable[[], None]` (por defecto `self.request_stop`; la tarea 4 lo cambia en escritorio). En `Application.run()`, si `should_check(...)`: `asyncio.create_task(self.updates.run())`, cancelada en el `finally`.

- [ ] **Step 1: Tests que fallan** (`FakeDownloader` con respuestas fijas, `asyncio.sleep` falso):
  - `test_newer_version_is_announced`: publica `UpdateStatus(version="0.23.0", state=AVAILABLE, notes=...)`.
  - `test_same_or_older_version_is_ignored`, `test_bad_signature_is_ignored` y `test_missing_manifest_is_silent` (`fetch` → `None`): no se publica ningún `UpdateStatus` (Review Focus 3).
  - `test_update_downloads_verifies_launches_and_quits`: orden `DOWNLOADING` (progreso) → `INSTALLING` → `launch_installer(path)` → `quit_app()`; `path` dentro de `download_dir` y con el nombre del manifiesto.
  - `test_request_in_unsafe_phase_is_refused`: con `ChampSelect` no descarga nada.
  - `test_phase_turns_unsafe_during_download`: la fase cambia a `ChampSelect` mientras se descarga → no lanza ni cierra; `FAILED` con un error que lo explique (Review Focus 2).
  - `test_failed_download_reports_and_allows_retry`: `to_file` lanza `UpdateError` → `FAILED` con error; una segunda petición vuelve a intentarlo.
  - `test_should_check_only_in_frozen_app_with_key`: sin congelar, con `update_check=False` o con clave vacía → `False` (Review Focus 4).
  - `test_update_check_setting` (en `tests/test_core.py` o en el nuevo archivo): `update_check = false` en `config.toml` → `False` y sin avisos.
- [ ] **Step 2:** comprobar que fallan.
- [ ] **Step 3:** implementar. La descarga va a `download_dir` (por defecto `Path(tempfile.gettempdir()) / "erizocoach-actualizar"`, vaciada antes). Solo una actualización a la vez. Los errores de red de `check()` se registran con `log.debug` y no se muestran.
- [ ] **Step 4:** pasan los tests nuevos y toda la batería (`uv run --frozen pytest -q`).
- [ ] **Step 5:** commit «Actualizar: comprobación periódica y descarga verificada».

### Task 4: La tarjeta «Actualización disponible» en la ventana

**Files:**
- Modify: `src/erizocoach/ui/web/presenter.py` (estado `update`, `_on_update`, `request_update`), `src/erizocoach/ui/desktop.py` (`DesktopBridge.update_app`; en `run()`, `self._app.quit = self.quit`), `src/erizocoach/ui/web/server.py` (comando `{"type": "updateApp"}`), `src/erizocoach/ui/web/static/app.js`, `src/erizocoach/ui/web/static/style.css`
- Test: `tests/test_ui.py` y `tests/test_desktop.py`

**Interfaces:**
- Consumes: Task 3 (`UpdateStatus`, `UpdateState`, `UpdateRequested`).
- Produces: `State["update"]`: `None` o `{"version", "notes", "state", "progress", "error"}`; `UiPresenter.request_update() -> bool` (publica `UpdateRequested`; `False` si no hay versión disponible).

- [ ] **Step 1: Tests que fallan.**
  - `test_update_status_reaches_the_window`: tras `UpdateStatus(...)` en el bus, `presenter.state["update"]` tiene la versión, el estado y el progreso.
  - `test_request_update_publishes_only_when_available`.
  - `test_bridge_update_app` (escritorio) y `test_ws_update_command` (navegador): ambos llaman a `request_update`.
- [ ] **Step 2:** comprobar que fallan.
- [ ] **Step 3:** implementar. En `app.js`, `renderUpdate(state)` se pone delante, como el aviso de estadísticas, solo si `state.update` y además `!state.connected` o la fase está en `["None", "Lobby", "EndOfGame"]`. Contenido:
  - `available` y `failed`: título «Erizo Coach X disponible», las notas y el botón «Actualizar».
  - `downloading`: barra con «Descargando… N %».
  - `installing`: «Instalando… Erizo Coach se volverá a abrir solo».
  - `failed`: además, el error.

  Desde el JS, el botón llama a `api.update_app()` en escritorio o envía `{type:"updateApp"}` por el websocket en modo navegador. Los estilos reutilizan las clases `card`, `cta` y la barra de progreso.
- [ ] **Step 4:** pasan los tests. Revisión visual con `--demo` y un `UpdateStatus` publicado a mano, capturada como las de la intro.
- [ ] **Step 5:** commit «Actualizar: tarjeta y botón en la ventana».

### Task 5: Instalador en modo `--actualizar <pid>`

**Files:**
- Modify: `packaging/installer.py`
- Test: `tests/test_installer.py`

**Interfaces:**
- Produces: `wait_for_exit(pid: int, timeout: float) -> bool` (ctypes `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject`; `True` si ya no existe) y la rama de `main()` para `["--actualizar", "<pid>"]`.

- [ ] **Step 1: Tests que fallan** (con `monkeypatch` de `wait_for_exit`, `install`, `message`, `subprocess.Popen` y `webview2_installed`, como los `run_main` actuales):
  - `test_update_mode_installs_without_asking_and_reopens`: sin el mensaje «¿Quieres instalarlo?»; llama a `install` y abre la app con `explorer.exe`; sin mensaje final.
  - `test_update_mode_waits_for_the_app`: `wait_for_exit(pid, 30)`.
  - `test_update_mode_app_still_open_warns_and_does_not_install`: `wait_for_exit` → `False` → mensaje de aviso y sin instalar (Review Focus 5).
  - `test_update_mode_bad_pid_argument`: `--actualizar abc` → código 1 y sin instalar.
- [ ] **Step 2:** comprobar que fallan.
- [ ] **Step 3:** implementar reutilizando `install`, `with_progress` e `install_webview2`.
- [ ] **Step 4:** pasan los tests.
- [ ] **Step 5:** commit «Instalador: modo actualización sin preguntas».

### Task 6: Script para publicar versiones

**Files:**
- Create: `scripts/publicar_version.py`
- Test: `tests/test_publicar_version.py` (importado con `importlib`, como `build_installer`)

**Interfaces:**
- Consumes: Task 1 (`Manifest`, `encode_manifest`, `sign`).
- Produces: estas opciones de línea de comandos:
  - `--crear-clave`: genera el par, guarda la privada en el keyring y reescribe `src/erizocoach/update/signing_key.py` con la pública en hexadecimal; se niega si ya existe la privada.
  - `--token`: pide el token con `getpass` y lo guarda en el keyring.
  - sin opciones: publica `__version__`.

  Funciones:
  - `build_manifest(installer: Path, version: str, notes: str, published: str) -> Manifest`.
  - `release_notes(tag: str) -> str` (asunto del mensaje de la etiqueta).
  - `preflight(version: str) -> list[str]` (problemas: cambios sin guardar, etiqueta inexistente o que no está en `origin`).
  - `class GitHubReleases(token: str, repo: str = "ericperezortuno/ErizoCoach", opener=urllib.request.urlopen)` con `release_exists(tag) -> bool`, `create_release(tag, name, body) -> int` y `upload_asset(release_id, path, name, content_type) -> None` (a `uploads.github.com/.../assets?name=`, con la cabecera `Authorization: Bearer`).

- [ ] **Step 1: Tests que fallan.**
  - `test_manifest_describes_the_installer`: tamaño y SHA-256 reales de un archivo temporal; nombre `Instalar-Erizo-Coach-X.exe`.
  - `test_preflight_lists_problems` (con un `run_git` falso).
  - `test_release_is_created_with_three_assets` (con un `opener` falso): crea la release `vX` y sube el manifiesto, la firma y el instalador con esos nombres; el token solo va en la cabecera `Authorization` y no aparece en la URL ni en los registros (`caplog`).
  - `test_existing_release_is_not_overwritten`.
- [ ] **Step 2:** comprobar que fallan.
- [ ] **Step 3:** implementar. Para publicar:
  1. `preflight`; sale con los problemas si hay alguno.
  2. `build_exe.py` y después `build_installer.py`.
  3. Copia `dist/Instalar Erizo Coach X.exe` como `Instalar-Erizo-Coach-X.exe`, escribe el manifiesto y lo firma.
  4. Crea la release y sube los tres archivos.
  5. Comprueba que se descarga `MANIFEST_URL` y que verifica con la clave pública.
- [ ] **Step 4:** pasan los tests.
- [ ] **Step 5:** commit «Script para publicar versiones firmadas en GitHub».

### Task 7: Clave real, documentación, versión 0.23.0 y primera release

**Files:**
- Modify: `src/erizocoach/update/signing_key.py` (generado), `README.md`, `docs/instalar-en-otro-pc.md`, `pyproject.toml`, `src/erizocoach/__init__.py`, `packaging/version_info.txt`, `uv.lock`

- [ ] **Step 1:** `uv run --frozen python scripts/publicar_version.py --crear-clave` desde la pestaña Terminal (keyring real). Comprobar que `PUBLIC_KEY` mide 32 bytes y que `cmdkey /list` muestra `ErizoCoach-publicar`.
- [ ] **Step 2:** documentar.
  - README: el botón y cómo publicar (token *fine-grained* con «Contents: Read and write» solo para `ErizoCoach`).
  - `instalar-en-otro-pc.md`: «Actualizar» con el botón y la página de Releases para la primera vez.
  - Versión 0.23.0 en los tres sitios.
  - Batería completa y `ruff` limpios.
- [ ] **Step 3:** commit, etiqueta `v0.23.0` y subida.
- [ ] **Step 4 (lo hace el usuario):** crear el token en GitHub y guardarlo con `publicar_version.py --token`.
- [ ] **Step 5:** `uv run --frozen python scripts/publicar_version.py`. Comprobar la release en GitHub y que el manifiesto descargado verifica.
- [ ] **Step 6:** instalar la 0.23.0 en el PC con `_reinstall.ps1`, comprobando antes la fase del cliente. Comprobar en el registro que la app ha consultado la release y que no ofrece actualizar, porque ya es la última.
