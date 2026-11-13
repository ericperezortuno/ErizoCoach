# Instalar LoL Coach en otro PC

Guía para pasarle LoL Coach a otra persona (por ejemplo, a un amigo para que lo pruebe).

## Para quien lo pasa

1. Construye la app y el instalador (en una terminal, en la carpeta del proyecto):

   ```bash
   uv run python scripts/build_exe.py
   uv run python scripts/build_installer.py
   ```

2. Envía el archivo `dist\Instalar LoL Coach <versión>.exe` (unos 32 MB) por Discord,
   Google Drive, WeTransfer o como prefieras.

No va dentro nada tuyo: ni tu clave de Riot (está cifrada en tu Windows), ni tus
estadísticas, ni tu configuración.

## Para quien lo instala

**Requisitos:** Windows 10 u 11 y League of Legends instalado. No hace falta Python ni
nada más.

1. Abre `Instalar LoL Coach <versión>.exe`.
2. Windows avisará con «Windows protegió su PC», porque el programa no está firmado
   (firmarlo cuesta dinero). Pulsa **Más información** → **Ejecutar de todas formas**.
3. Acepta la instalación. Se instala **solo para tu usuario**, sin pedir permisos de
   administrador, y crea accesos directos en el menú Inicio y en el escritorio.
4. Abre **LoL Coach** y después el cliente de League of Legends (o al revés: se conecta
   solo).

### Qué hace sin configurar nada

- Al preseleccionar un campeón, su ventana ya enseña las runas y hechizos que le pondrá.
- Al hacerse el último pick de la selección de campeones, pone **runas y hechizos**
  (recomendación oficial de Riot ajustada a tus rivales) y una **build en la tienda**
  (conjunto de objetos «LoL Coach: campeón»).
- Durante la partida, en su ventana: **qué comprar ahora** y **quién va fuerte**.
- Usa una sola página de runas propia, llamada «LoL Coach». Si no tienes hueco para
  páginas, renombra una a «LoL Coach» y la usará; nunca toca tus otras páginas.

### Builds con estadísticas de alto elo

No hay que hacer nada: LoL Coach descarga solo las **estadísticas compartidas** (builds,
runas y hechizos de partidas reales de alto elo de EUW, sin datos de ningún jugador) y las
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
   "%LOCALAPPDATA%\Programs\LoL Coach\lolcoach-consola.exe" stats clave
   ```

   Pega la clave (no se ve al escribir) y pulsa Intro. Con LoL Coach abierto, las
   estadísticas se descargan solas en segundo plano.
3. Si no juegas en EUW, crea `%LOCALAPPDATA%\LoLCoach\config.toml` con la línea
   `riot_region = "na1"` (o `eun1`, `tr1`, `kr`).

### Actualizar y desinstalar

- **Actualizar:** abre el instalador de la versión nueva con LoL Coach cerrado.
- **Desinstalar:** *Configuración → Aplicaciones → Aplicaciones instaladas → LoL Coach →
  Desinstalar*. Se quitan la app y los accesos directos; los datos
  (`%LOCALAPPDATA%\LoLCoach`) se conservan por si vuelves a instalarlo.
