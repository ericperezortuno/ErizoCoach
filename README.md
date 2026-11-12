# LoL Coach

Asistente local para League of Legends que te pone las runas y hechizos de
invocador, te propondrá la build y la adaptará a la partida.

## Estado: Fase 2 (runas y hechizos automáticos)

### Lo que hace

- **Detecta sola el cliente de LoL** (aunque lo abras después que la app, o lo cierres y lo
  vuelvas a abrir) y se conecta de forma **segura**, verificada con el certificado oficial de Riot.
- **Sigue la selección de campeones en tiempo real**: tu posición, quién bloquea qué campeón,
  baneos, intercambios, dodges y el momento en que **se hace el último pick**.
- **Al hacerse el último pick, te pone las runas y los hechizos** (en el mismo segundo):
  1. **Base**: la recomendación oficial de Riot para tu campeón, posición y mapa (se
     actualiza sola con cada parche).
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

## Cómo se usa

Requisitos: Windows y [uv](https://docs.astral.sh/uv/) (ya lo tienes instalado).

Doble clic en **`Iniciar LoL Coach.bat`**, o desde una terminal en esta carpeta:

```bash
uv run lolcoach
```

Otras opciones:

```bash
uv run lolcoach --check     # comprueba la conexión con el cliente y sale
uv run lolcoach --debug     # muestra también los mensajes técnicos
```

Los registros se guardan en `%LOCALAPPDATA%\LoLCoach\logs\lolcoach.log`
(máximo 4 MB, rotativos). Configuración opcional en `%LOCALAPPDATA%\LoLCoach\config.toml`:

```toml
league_dir = "D:/Riot Games/League of Legends"  # solo si no se detecta sola
debug = false
discovery_interval = 3  # segundos entre intentos de detectar el cliente
auto_runes = true       # false = solo recomendar las runas, sin aplicarlas
auto_spells = true      # false = solo recomendar los hechizos, sin aplicarlos
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
  Riot o no se envía nada.
- Solo puede haber una copia abierta (evita aplicar runas dos veces).

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
  ui/                  Presentación (consola; en la fase 4, ventana del segundo monitor)
tests/                 Pruebas automáticas: cliente de LoL simulado y datos reales del parche
```

Cada fase nueva se engancha a los eventos existentes (`PicksFinalized`,
`MyChampionChanged`, `LoadoutApplied`, `ChampSelectEnded`...) sin modificar lo anterior.
Las reglas de `loadout/rules.py` son funciones puras: se pueden ampliar, o sustituir por
IA en un plan de pago, sin tocar el resto.

## Desarrollo

```bash
uv run pytest          # pruebas
uv run ruff check .    # revisión de estilo y errores comunes
```

## Hoja de ruta

1. **Conexión y lectura de la selección de campeones** ✅
2. **Runas y hechizos automáticos al hacerse el último pick** ✅
3. Build inicial en la tienda, adaptada a la composición enemiga.
4. Ventana en el segundo monitor durante la partida: quién va fuerte y qué comprar.
5. Mejoras: IA opcional (de pago), historial propio, pulido visual.
