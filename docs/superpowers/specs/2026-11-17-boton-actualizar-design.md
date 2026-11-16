# Botón «Actualizar» con las Releases de GitHub — diseño

Fecha: 2026-11-17 · Estado: pendiente de aprobación

## Para qué

Que quien tenga Erizo Coach instalado (tú y tus amigos) pase a la última versión con un clic
desde la propia app, sin que tengas que mandar el `.exe` cada vez. Cuando publicamos una
versión nueva en GitHub, las apps instaladas la ven y ofrecen actualizar.

## Objetivos, no objetivos y anti-objetivos

- **Objetivos:** aviso con botón en la ventana; un clic descarga, verifica, cierra la app,
  instala y la vuelve a abrir; publicar una versión es un solo comando en tu PC.
- **No objetivos:** actualizarse sin clic; actualizar instalaciones hechas desde el código
  (`uv run`); volver a una versión anterior; versiones de prueba (beta).
- **Anti-objetivos (fallaría aunque funcionase):** instalar algo que no hayas firmado tú;
  cerrar la app en la selección de campeón, en cola o en partida; tocar los datos del
  usuario; que el token de GitHub o la clave de firma acaben en el repo, en el `.exe` o en
  los registros.

## Enfoques considerados

1. **Releases de GitHub en `ErizoCoach` (elegido).** Cada versión es una release con el
   instalador adjunto. Página de descargas pública para amigos nuevos y una dirección fija
   para «la última»: `…/releases/latest/download/<archivo>`. Publicar necesita un permiso
   de la API de GitHub en tu PC.
2. **Rama `versiones` con el último instalador.** Sirve con la clave SSH que ya hay, pero
   cada versión deja 34 MB en el repo y queda menos ordenado.
3. **Rama en `ErizoCoach-datos`.** Era la opción mientras el código era privado; al hacerse
   público, deja de tener sentido separar los instaladores del código.

## Cómo funciona

### Publicar una versión (tu PC)

Tras el commit + etiqueta + subida de siempre, `uv run python scripts/publicar_version.py`:

1. Comprueba que no hay cambios sin guardar y que la etiqueta `vX.Y.Z` (= `__version__`)
   existe y ya está en GitHub.
2. Construye la app y el instalador, en ese orden (`build_exe.py` y luego
   `build_installer.py`).
3. Escribe el manifiesto `erizocoach-version.json`: versión, nombre del instalador, tamaño,
   SHA-256, notas (la primera línea del mensaje de la etiqueta) y fecha.
4. Lo firma con **Ed25519** → `erizocoach-version.json.sig`.
5. Crea la release `vX.Y.Z` con el instalador, el manifiesto y la firma, por la API de
   GitHub.

**Permiso para publicar (mi elección):** un **token *fine-grained*** de GitHub solo para el
repo `ErizoCoach`, con el permiso *Contents: Read and write*. Se guarda cifrado en el
Administrador de credenciales de Windows, como la clave de Riot: lo pegas una vez con
`publicar_version.py --token`. Lo prefiero a GitHub CLI porque `gh` pide permisos amplios
sobre todos tus repos; el token solo puede tocar este. No tienes que instalar nada.

**Clave de firma:** se crea una vez con `publicar_version.py --crear-clave`. La privada se
guarda cifrada en el Administrador de credenciales y nunca sale de tu PC. La pública va en
el código (`src/erizocoach/update/signing_key.py`), que es su sitio.

### Comprobar si hay versión nueva (la app)

- Solo en la app instalada (el `.exe`), nunca desde el código. Mira 30 s después de abrirse
  y luego cada 6 horas.
- Descarga `…/releases/latest/download/erizocoach-version.json` y su `.sig`. No usa la API
  de GitHub, así que no le afectan sus límites de peticiones.
- Comprueba la firma con la clave pública incluida. Si no cuadra o falta algo, lo ignora y
  lo anota en el registro.
- Si la versión es mayor que la instalada, la ventana muestra el aviso.
- Solo HTTPS y solo `github.com` más los dominios de descarga de GitHub
  (`*.githubusercontent.com`). El manifiesto puede ocupar como mucho 64 KB, y el instalador
  como mucho 200 MB y el tamaño exacto del manifiesto.

### El botón (la ventana)

- Aviso: «Erizo Coach 0.23.0 disponible · *notas*» con el botón **Actualizar**.
- **Visible solo** con el LoL cerrado, en el menú o en la sala (y al terminar una partida).
  Se oculta en cola, en «partida encontrada», en la selección de campeón y en partida: con
  el botón oculto no se puede cerrar la app en mal momento.
- Al pulsarlo: descarga el instalador a una carpeta temporal (progreso en el propio botón),
  comprueba tamaño y SHA-256 contra el manifiesto firmado, lo abre en modo actualización y
  la app se cierra sola.
- Si algo falla, lo dice en el aviso y el botón vuelve a estar disponible.

### El instalador en modo actualización

`Instalar Erizo Coach X.exe --actualizar <pid>`:

- No pregunta «¿quieres instalarlo?», porque ya has pulsado Actualizar.
- Espera como mucho 30 s a que se cierre la app (el proceso `<pid>`).
- Instala con su ventanita de progreso; WebView2 se comprueba igual que siempre.
- Vuelve a abrir Erizo Coach a través del Explorador. Solo enseña un mensaje si algo falla.

### Ajuste

`update_check = true` en `config.toml`; con `false`, la app no comprueba nunca.

## Restricciones

- **Seguridad:** la firma no depende de GitHub. Aunque alguien entrara en tu cuenta o
  robara el token, no podría publicar una actualización que las apps aceptaran. El token y
  la clave privada no se escriben nunca en registros (se añaden al filtro de secretos). El
  instalador sigue sin pedir administrador.
- **Dependencia nueva:** `cryptography`, para verificar Ed25519. Ya está en el proyecto por
  las pruebas; añade unos 5 MB al `.exe`.
- **Pruebas:** sin red ni GitHub real (servidores falsos), como el resto del proyecto.
- **Transición:** las versiones antiguas no tienen botón. Quien tenga la 0.22.0 (tu amigo)
  instala una vez a mano la primera versión con botón, desde la página de Releases.

## Lo que queda al constructor

Nombres de módulos, eventos y campos del estado de la ventana; textos exactos; cómo se
cierra la app en modo escritorio y en modo navegador; la carpeta temporal de descarga.
