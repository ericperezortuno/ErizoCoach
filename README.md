# LoL Coach

Asistente local para League of Legends que te pone las runas, los hechizos de
invocador y la build en la tienda, adaptados a cada partida, y durante la partida te
dice en tu segundo monitor qué comprar y quién va fuerte.

## Estado: v0.8.1 (segundo plano, equipos por línea, con runas, hechizos y builds de alto elo)

### Lo que hace

- **Detecta sola el cliente de LoL** (aunque lo abras después que la app, o lo cierres y lo
  vuelvas a abrir) y se conecta de forma **segura**, verificada con el certificado oficial de Riot.
- **Sigue la selección de campeones en tiempo real**: tu posición, quién bloquea qué campeón,
  baneos, intercambios, dodges y el momento en que **se hace el último pick**.
- **Al hacerse el último pick, te pone las runas y los hechizos** (en el mismo segundo):
  1. **Base**: las runas y hechizos que más usan los jugadores de alto elo con tu campeón
     y posición (si hay estadísticas suficientes, ver abajo); si no, la recomendación
     oficial de Riot para tu campeón, posición y mapa (se actualiza sola con cada parche).
  2. **Rival de línea por probabilidad**: se prueban todos los repartos posibles de los
     enemigos en las líneas según las posiciones habituales de cada campeón.
  3. **Ajustes explicados**, solo cuando hay un motivo claro:

     | Situación | Ajuste |
     |---|---|
     | Equipo enemigo con mucho control | Fragmento de Tenacidad |
     | Eres frágil y tu rival de línea es un asesino de daño explosivo | Fragmento de Vida |
     | Llevas la fila 2 de Valor | Revestimiento de huesos (vs. asesinos) o Fuerzas renovadas (vs. castigo a distancia) |
     | Tirador contra 3+ campeones con control fuerte | Limpiar |
     | Apoyo contra 2+ asesinos muy móviles | Extenuación |
     | Mago frágil y sin movilidad contra un asesino en medio | Barrera en vez de Teleportar |
     | Jungla | Aplastar, siempre |

  4. **Si no se ven los enemigos** (selección a ciegas) **o no está claro tu rival**, se
     usan las runas y hechizos por defecto para tu campeón, sin ajustes de línea.
- **Destello se queda en la tecla en la que lo tienes** (D o F).
- **Puedes cambiar runas y hechizos a mano**: la app no vuelve a tocarlos en esa selección.
  Solo vuelve a aplicar si cambia tu campeón (intercambio o banquillo de ARAM).
- En ARAM usa las recomendaciones de ARAM (Destello + Marca). En modos sin runas no toca nada.

### Tu página de runas

La app usa **una sola página propia**, llamada «LoL Coach: (campeón)»:

- Si existe, la reescribe. Si no, la crea si tienes un hueco libre.
- Si no hay hueco, **solo reutiliza una página vacía y no válida** (sin nombre).
  Nunca toca tus otras páginas.
- Si no hay ninguna opción, no cambia las runas: te las muestra para que las pongas tú
  (los hechizos sí se aplican). Para darle una página, renombra una a «LoL Coach».

### La build en la tienda

En el mismo momento, la app escribe un **conjunto de objetos «LoL Coach: (campeón)»**, que
verás en la tienda de la partida, pestaña *Conjuntos de objetos*:

| Bloque | Contenido |
|---|---|
| Inicio | Objetos iniciales según tu posición (Doran, objeto de apoyo, mascota de jungla, objeto de guardián en ARAM) |
| Botas | Las de tu tipo de campeón (o las defensivas, si eres de primera línea y hace falta) |
| Núcleo (en orden) | Los 3 objetos clave, en orden de compra |
| Contra este equipo | Objetos que pide el equipo enemigo, con su componente barato delante |
| Build completa | 6 objetos: botas + núcleo + los 2 mejores huecos según los enemigos |
| Mejora de apoyo | La mejora del Atlas mundial adecuada (solo apoyos) |

**De dónde sale la build** (Riot no ofrece builds recomendadas en el cliente):

1. **Tipo de campeón**: con su perfil oficial (roles, tipo de daño) se clasifica en uno de 10
   tipos (mago de daño explosivo, tirador de críticos, luchador, tanque, apoyo encantador...).
   Hay excepciones para campeones con builds especiales (Smolder, Kai'Sa, Ezreal, Jhin...).
2. **Build base del tipo**, con todos sus objetos **validados contra el parche actual**: si
   Riot retira uno, se descarta y se completa con los siguientes.
3. **Adaptación a los enemigos**, con el motivo en la consola:

   | Situación | Qué añade (según tu tipo de campeón) |
   |---|---|
   | Enemigos que se curan mucho (Soraka, Aatrox, Vladimir...) | Antisanación: Morellonomicón, Recordatorio letal, Mecanoespada punki o Malla de espinas |
   | Supresiones (Malzahar, Skarner, Warwick, Urgot) | Cimitarra mercurial / Bendición de Mikael |
   | 3+ enemigos de daño mágico | Velo del hada, Cimitarra, Fauces, Rostro espiritual, Rookern kaénico... |
   | 3+ enemigos de daño físico | Zhonya, Ángel de la guarda, Baile de la muerte, Randuin... |
   | 2+ asesinos muy móviles | Zhonya, Arcoescudo inmortal, Bendición de Mikael |
   | 2+ tanques | Bastón del Vacío, Recuerdos de lord Dominik, Cuchilla negra |
   | 2+ campeones con muchos escudos | Colmillo de serpiente |
   | Eres de primera línea | Botas de mercurio o blindadas según el daño enemigo |

Si has descargado estadísticas (ver abajo), la build base de cada campeón y posición con
suficientes partidas es **la que de verdad hacen los jugadores de alto elo**; las reglas
quedan como respaldo para campeones con pocos datos y para ARAM.

### Builds con estadísticas reales (API oficial de Riot)

1. **Clave**: consigue tu clave gratuita en [developer.riotgames.com](https://developer.riotgames.com)
   y guárdala con `uv run lolcoach stats clave`. Se pide sin mostrarse y se guarda **cifrada en
   el Administrador de credenciales de Windows**. Nunca aparece en pantalla ni en los registros.
   La de desarrollo caduca cada 24 h; para no renovarla, pide una clave personal siguiendo
   [docs/clave-personal-riot.md](docs/clave-personal-riot.md).
2. **Descarga automática**: con la app abierta, descarga sola en segundo plano (unas 1.500
   partidas por hora) hasta tener 3.000 partidas del parche. **Se pausa en la selección de
   campeones y durante la partida** y sigue al volver al menú. Recorre jugadores Challenger,
   Gran Maestro y Maestro de tu región, toma sus partidas clasificatorias del parche actual y
   guarda de cada jugador solo campeón, posición, resultado, compras en orden, runas y hechizos
   (**sin nombres, Riot ID ni identificadores**). Respeta los límites de Riot y no repite
   partidas. Con un parche nuevo vuelve a empezar y borra los parches viejos. Si la clave
   caduca, te avisa en la ventana. También se puede lanzar a mano con
   `uv run lolcoach stats descargar` (con la app cerrada).
3. **Uso**: tras cada tanda de descarga se recalcula, sin reiniciar, la build más habitual de
   cada campeón y posición (inicio, botas, los 3 primeros objetos **en su orden real** y los
   siguientes), sus runas (la piedra angular más elegida y, entre quienes la llevan, lo más
   elegido de cada fila) y sus hechizos. Con 20 partidas o más sustituyen a las reglas y a la
   recomendación de Riot; los ajustes contra los enemigos se aplican encima. Si el parche es
   muy nuevo y aún hay pocas partidas, usa las del anterior.

La consola y la ventana indican de dónde salen las runas y la build, por ejemplo
«estadísticas de 143 partidas de alto elo (parche 16.22)».

**Tus conjuntos de objetos están a salvo**: el cliente solo permite reemplazar la lista
entera, así que la app la lee, cambia solo el conjunto «LoL Coach» y comprueba después que
los tuyos siguen ahí. Si faltara alguno, restaura la lista original.

### La ventana del segundo monitor

LoL Coach es una **aplicación de escritorio**: su ventana (con su nombre e icono) se abre
**a toda altura en tu segundo monitor**, pegada al principal, y al cerrarla se cierra la app.
Muestra:

- **En la selección de campeones**: tu campeón, las runas y hechizos aplicados con sus
  motivos, y la build (inicio, núcleo, completa y «contra este equipo»).
- **Durante la partida** (se actualiza cada 3 segundos):
  - **Compra ahora**: el siguiente objeto, el porqué, cuánto oro te falta y qué
    componentes ya te puedes comprar (bajando por la receta si hace falta).
  - **Tu build**: lo que ya tienes marcado y lo siguiente resaltado.
  - **Rivales**: KDA, súbditos, nivel, tipo de daño, «Tu rival» y una etiqueta
    **Muy fuerte / Fuerte / Normal / Por detrás**.
  - **Tu equipo**, con la misma información.
  - Los dos equipos van **por línea** (superior, jungla, medio, inferior, apoyo), así que
    cada fila enfrenta a los dos jugadores de esa línea.

**Segundo plano**: el botón **Segundo plano** de la cabecera esconde la ventana (tampoco sale
en la barra de tareas) y LoL Coach **sigue funcionando igual**: runas, hechizos, build en la
tienda y descarga de estadísticas. Mientras tanto la ventana no se repinta, así que no gasta.
Para volver, haz clic en el **icono de LoL Coach junto al reloj** de Windows (puede estar dentro
de la flechita ˄) o abre LoL Coach otra vez. Con clic derecho en ese icono también puedes
cerrarlo. La primera vez que lo escondes, Windows te avisa de que sigue en marcha.

**Quién va fuerte**: el oro de los rivales no se ve, pero sus objetos sí. Se combina el valor
de sus objetos (60 %), su KDA (25 %) y su nivel (15 %) frente a la media de la partida. En los
primeros 3 minutos no se etiqueta a nadie.

**Qué comprar**: tu build (primer objeto del núcleo, botas, resto) con dos excepciones:
- Si un enemigo que **hace mucho daño** va muy fuerte (o tu rival de línea va fuerte) y no
  tienes defensa contra su tipo de daño, el objeto defensivo de tu tipo de campeón va primero.
- Si alguien que **se cura mucho** va fuerte, la antisanación (su componente barato) va primero.
- Si se dan las dos, manda la que responde al enemigo más fuerte.

**Diseño**: estilo *liquid glass* con los colores de League (dorado y azul marino):
tarjetas translúcidas con desenfoque y brillo en los bordes sobre un fondo con profundidad.
Todo el texto cumple el contraste AA. Las animaciones son cortas y ligeras para no restar
rendimiento a la partida; se desactivan si Windows tiene reducidos los efectos de movimiento,
y la transparencia se quita si pides menos transparencia.

Usa la API oficial de Riot de la partida (`127.0.0.1:2999`, verificada con su certificado)
y **solo información visible en el marcador**: está permitido por las normas de Riot.

**Seguridad de la ventana**: es una ventana nativa (pywebview, con el motor WebView2 que
Windows 11 trae de serie) que habla con la app **directamente, sin servidor ni puerto abierto**.
La página solo puede pedir tres cosas (el estado, el icono de un objeto o campeón por su id y
pasar a segundo plano) y
se carga con una política estricta: solo su propio código (con un *nonce* nuevo en cada
arranque), iconos incrustados y ninguna conexión de red.

Si faltara WebView2, LoL Coach usa como respaldo Edge en modo aplicación con un servidor local
(solo `127.0.0.1`, clave aleatoria, `Host` y `Origin` comprobados y cabeceras estrictas).

## Cómo se usa

**Con la aplicación instalada**: abre **LoL Coach** desde el menú Inicio o el acceso directo del
escritorio. Para salir, cierra su ventana (o, si está en segundo plano, clic derecho en su
icono junto al reloj → *Cerrar LoL Coach*).

**Instalar o actualizar** (desde una terminal en esta carpeta; requiere [uv](https://docs.astral.sh/uv/)):

```bash
uv run python scripts/make_icon.py   # (solo si cambias el icono) assets/lolcoach.ico
uv run python scripts/build_exe.py   # crea dist/LoL Coach/ con los ejecutables
uv run python scripts/install.py     # lo instala con accesos directos (--desinstalar para quitarlo)
uv run python scripts/build_installer.py  # instalador de un archivo para pasárselo a otra persona
```

**Pasárselo a otra persona**: `scripts/build_installer.py` crea `dist\Instalar LoL Coach
<versión>.exe`, un solo archivo que se instala con doble clic (sin permisos de administrador)
y se desinstala desde *Aplicaciones instaladas*. Guía para quien lo instala:
[docs/instalar-en-otro-pc.md](docs/instalar-en-otro-pc.md).

Se instala en `%LOCALAPPDATA%\Programs\LoL Coach` (sin permisos de administrador) con dos
ejecutables: **`LoL Coach.exe`** (la aplicación, sin consola) y **`lolcoach-consola.exe`** (los
comandos de texto, por ejemplo `lolcoach-consola.exe stats descargar`). Tus datos están aparte, en
`%LOCALAPPDATA%\LoLCoach`, y no se tocan al actualizar ni al desinstalar.

**Desde el código** (desarrollo):

```bash
uv run lolcoach             # la app con su ventana de escritorio
uv run lolcoach --check     # comprueba la conexión con el cliente y sale
uv run lolcoach --debug     # muestra también los mensajes técnicos
uv run lolcoach --demo      # abre la ventana con una partida de ejemplo (para verla sin jugar)
uv run lolcoach --browser   # ventana con Edge en lugar de la de escritorio
uv run lolcoach --no-window # sin ventana (solo consola)
uv run lolcoach stats clave          # guarda tu clave de la API de Riot (cifrada)
uv run lolcoach stats descargar      # descarga partidas de alto elo (--horas 2, --partidas N)
uv run lolcoach stats estado         # cuántas partidas hay descargadas
uv run lolcoach stats olvidar-clave  # borra la clave guardada
```

Con la ventana de escritorio, al cerrarla se cierra LoL Coach. En modo `--browser` o
`--no-window` se sale con **Ctrl+C**.

Los registros se guardan en `%LOCALAPPDATA%\LoLCoach\logs\lolcoach.log`
(máximo 4 MB, rotativos). Configuración opcional en `%LOCALAPPDATA%\LoLCoach\config.toml`:

```toml
league_dir = "D:/Riot Games/League of Legends"  # solo si no se detecta sola
debug = false
discovery_interval = 3  # segundos entre intentos de detectar el cliente
auto_runes = true       # false = solo recomendar las runas, sin aplicarlas
auto_spells = true      # false = solo recomendar los hechizos, sin aplicarlos
auto_item_set = true    # false = solo mostrar la build, sin ponerla en la tienda
window = true           # false = sin ventana del segundo monitor
window_on_secondary = true  # false = abrir la ventana en el monitor principal
window_mode = "escritorio"  # "navegador" = usar Edge en lugar de la ventana de escritorio
window_on_top = false       # true = ventana siempre encima
live_poll_interval = 3  # segundos entre lecturas de la partida (1-10)
riot_region = "euw1"    # región de las estadísticas: euw1, eun1, tr1, na1, kr
stats_min_games = 20    # partidas mínimas para usar la build de estadísticas (5-500)
stats_auto_download = true   # descargar estadísticas solo, con la app abierta
stats_target_matches = 3000  # partidas por parche a partir de las que se para (200-50000)
```

## Seguridad y normas de Riot

- Solo usa la API **local y oficial** del cliente (`127.0.0.1`). No lee la memoria del
  juego, no inyecta nada y no automatiza acciones dentro de la partida: es compatible
  con Vanguard y con las normas de Riot para aplicaciones de terceros.
- No se envía nada a Internet. Todos los datos (campeones, runas, recomendaciones) los da
  el propio cliente, en tu idioma y de tu parche.
- La conexión TLS **solo confía en la autoridad de certificación de Riot**
  (`src/lolcoach/lcu/certs/riotgames.pem`) y verifica que el certificado es para
  `127.0.0.1`. Si otro programa intentara hacerse pasar por el cliente, se rechaza.
- La contraseña temporal del cliente nunca se muestra ni se guarda en los registros.
  La sesión de la selección (que incluye tu Riot ID y credenciales del chat) tampoco.
- Cada página de runas se **valida antes de enviarla**: si algo no cuadra, se usa la de
  Riot o no se envía nada. Cada objeto de la build se valida contra el parche.
- Tus páginas de runas y tus conjuntos de objetos no se tocan (ver arriba).
- Solo puede haber una copia abierta (evita aplicar runas dos veces).
- Una prueba automática comprueba, con el cliente abierto, que su contraseña temporal no
  aparece en ningún archivo del proyecto.

## Estructura

```
src/lolcoach/
  __main__.py          Punto de entrada y opciones
  app.py               Montaje de módulos y diagnóstico (--check)
  bus.py               Bus de eventos interno (los módulos se comunican por eventos)
  events.py            Catálogo de eventos: el "contrato" entre fases
  monitor.py           Traduce los eventos del cliente en eventos de la app
  lcu/                 Conexión con el cliente: detección, TLS, HTTP, websocket, reconexión
  champselect/         Modelo, lectura y seguimiento de la selección de campeones
  gamedata/            Datos del juego leídos del cliente: campeones, perfiles, runas, hechizos
  loadout/             Fase 2: posiciones, reglas, recomendación y aplicación de runas/hechizos
  builds/              Fase 3: tipos de campeón, builds base, adaptación y conjunto de objetos
  ingame/              Fase 4: lectura de la partida en vivo, quién va fuerte y qué comprar
  stats/               Fase 5: clave, API de Riot, descarga (también automática), agregación
                       y fuente de builds, runas y hechizos
  ui/console.py        Mensajes en consola
  ui/desktop.py        Ventana de escritorio (pywebview) y puente directo con la app
  ui/tray.py           Icono en la bandeja de Windows para el segundo plano
  ui/web/              Interfaz (HTML/CSS/JS), presentador, iconos, servidor de respaldo y demo
assets/                Icono de la aplicación (generado con scripts/make_icon.py)
packaging/             PyInstaller, información de versión del .exe e instalador (installer.py)
scripts/               Construir el .exe, instalarlo, crear el instalador y generar el icono
tests/                 Pruebas automáticas: cliente de LoL simulado y datos reales del parche
docs/                  Guías (clave personal de Riot, instalar en otro PC)
```

Cada fase nueva se engancha a los eventos existentes (`PicksFinalized`,
`MyChampionChanged`, `LoadoutApplied`, `BuildApplied`, `ChampSelectEnded`...) sin modificar
lo anterior. Las reglas (`loadout/rules.py`, `builds/rules.py`) son funciones puras: se
pueden ampliar, o sustituir por IA en un plan de pago, sin tocar el resto. La build base
llega por `BuildSource`: hoy, estadísticas reales con las reglas como respaldo.

## Desarrollo

```bash
uv run pytest          # pruebas
uv run ruff check .    # revisión de estilo y errores comunes
```

## Hoja de ruta

1. **Conexión y lectura de la selección de campeones** ✅
2. **Runas y hechizos automáticos al hacerse el último pick** ✅
3. **Build inicial en la tienda, adaptada a la composición enemiga** ✅
4. **Ventana en el segundo monitor durante la partida: quién va fuerte y qué comprar** ✅
5. **Builds afinadas con estadísticas reales de alto elo (API oficial de Riot)** ✅
6. **Runas y hechizos de alto elo, descarga automática y guía de clave personal** ✅
7. **Diseño liquid glass de la ventana** ✅
8. **Segundo plano con icono en la bandeja y equipos ordenados por línea** ✅
9. Siguientes mejoras posibles: IA opcional (de pago), historial de tus partidas.
