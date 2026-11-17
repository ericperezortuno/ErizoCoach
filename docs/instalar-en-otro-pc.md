# Instalar Erizo Coach en otro PC

Guía para pasarle Erizo Coach a otra persona (por ejemplo, a un amigo para que lo pruebe).

## Para quien lo pasa

Lo más fácil: pásale el enlace de la última versión,
<https://github.com/eriizzo/ErizoCoach/releases/latest>, y que descargue
`Instalar-Erizo-Coach-<versión>.exe` (unos 35 MB). Lleva todo dentro; lo que falte en su PC lo
descarga él solo. A partir de ahí se actualiza desde la propia app.

También puedes construirlo tú y enviarle el archivo por Discord, Google Drive, WeTransfer o
como prefieras (en una terminal, en la carpeta del proyecto):

```bash
uv run python scripts/build_exe.py
uv run python scripts/build_installer.py
```

y envía **solo** `dist\Instalar Erizo Coach <versión>.exe`.

No va dentro nada tuyo: ni tu clave de Riot (está cifrada en tu Windows), ni tus
estadísticas, ni tu configuración.

## Para quien lo instala

**Requisitos:** Windows 10 u 11 de 64 bits, League of Legends instalado e internet. No
hace falta instalar Python ni nada más a mano.

1. Abre `Instalar Erizo Coach <versión>.exe`.
2. Windows avisará con «Windows protegió su PC», porque el programa no está firmado
   (firmarlo cuesta dinero). Pulsa **Más información** → **Ejecutar de todas formas**.
3. Acepta la instalación. Se instala **solo para tu usuario**, sin pedir permisos de
   administrador, y crea accesos directos en el menú Inicio y en el escritorio.
   - Si a tu PC le falta **WebView2** (el componente de Microsoft con el que se dibuja la
     ventana; viene de serie en Windows 11 y en casi todos los Windows 10), el instalador
     te avisa, lo descarga de Microsoft, comprueba que está firmado por Microsoft y lo
     instala (unos minutos). Si no puede, Erizo Coach funciona igual y abre su ventana con
     Microsoft Edge.
4. Abre **Erizo Coach** y después el cliente de League of Legends (o al revés: se conecta
   solo).

### Qué hace sin configurar nada

- Al preseleccionar un campeón, su ventana ya enseña las runas y hechizos que le pondrá.
- Al hacerse el último pick de la selección de campeones, pone **runas y hechizos**
  (recomendación oficial de Riot ajustada a tus rivales) y una **build en la tienda**
  (conjunto de objetos «Erizo Coach: campeón»).
- Durante la partida, en su ventana: **qué comprar ahora** y **quién va fuerte**.
- Usa una sola página de runas propia, llamada «Erizo Coach». Si no tienes hueco para
  páginas, renombra una a «Erizo Coach» y la usará; nunca toca tus otras páginas.

### Builds con estadísticas de alto elo

No hay que hacer nada: Erizo Coach descarga solo las **estadísticas compartidas** (builds,
runas y hechizos de partidas reales de Diamante II a Challenger de EUW, Corea y
Norteamérica, sin datos de ningún jugador) y las
actualiza cada pocas horas. Si un día no hay internet, usa la última copia; si nunca llegó
a descargarla, las builds salen de reglas por tipo de campeón.

**Opcional, estadísticas propias:** si quieres descargarlas tú mismo (por ejemplo, de otra
región), hace falta una clave de Riot **propia** (las claves son personales y no se pueden
compartir):

1. Entra en [developer.riotgames.com](https://developer.riotgames.com) con tu cuenta de
   Riot y copia tu clave (la de desarrollo caduca cada 24 h; para una que no caduque,
   mira [clave-personal-riot.md](clave-personal-riot.md)).
2. En una terminal:

   ```bash
   "%LOCALAPPDATA%\Programs\Erizo Coach\erizocoach-consola.exe" stats clave
   ```

   Pega la clave (no se ve al escribir) y pulsa Intro. Con Erizo Coach abierto, las
   estadísticas se descargan solas en segundo plano.
3. Si no juegas en EUW, crea `%LOCALAPPDATA%\ErizoCoach\config.toml` con la línea
   `riot_region = "na1"` (o `eun1`, `tr1`, `kr`). Por defecto descarga de EUW, Corea y
   Norteamérica a la vez; para cambiarlo, `stats_regions = ["euw1"]`.

### Actualizar y desinstalar

- **Actualizar:** desde la 0.23.0, cuando hay una versión nueva la ventana enseña
  «Erizo Coach X disponible» con el botón **Actualizar** (con el LoL cerrado, en el menú o en
  la sala). Un clic y se descarga, se comprueba que es la versión firmada por el autor, se
  instala sola y Erizo Coach se vuelve a abrir. Con una versión anterior a la 0.23.0, descarga
  una vez a mano la última de <https://github.com/eriizzo/ErizoCoach/releases/latest> y abre el
  instalador con Erizo Coach cerrado; desde entonces ya se actualiza con el botón.
- **Desinstalar:** *Configuración → Aplicaciones → Aplicaciones instaladas → Erizo Coach →
  Desinstalar*. Se quitan la app y los accesos directos; los datos
  (`%LOCALAPPDATA%\ErizoCoach`) se conservan por si vuelves a instalarlo.
