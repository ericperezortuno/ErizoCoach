# LoL Coach

Asistente local para League of Legends que te pone las runas, los hechizos de
invocador y la build en la tienda, adaptados a cada partida, y durante la partida te
dice en tu segundo monitor qué comprar y quién va fuerte.

## Estado: Fase 4 (ventana en el segundo monitor durante la partida)

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

Es una base **provisional por reglas**: el siguiente paso previsto es afinarla por campeón
con estadísticas reales de partidas de alto elo (API oficial de Riot), sin cambiar el resto.

**Tus conjuntos de objetos están a salvo**: el cliente solo permite reemplazar la lista
entera, así que la app la lee, cambia solo el conjunto «LoL Coach» y comprueba después que
los tuyos siguen ahí. Si faltara alguno, restaura la lista original.

### La ventana del segundo monitor

Al arrancar, LoL Coach abre una ventana (Edge en modo aplicación, sin barras) **a toda
altura en tu segundo monitor**, pegada al principal. Muestra:

- **En la selección de campeones**: tu campeón, las runas y hechizos aplicados con sus
  motivos, y la build (inicio, núcleo, completa y «contra este equipo»).
- **Durante la partida** (se actualiza cada 3 segundos):
  - **Compra ahora**: el siguiente objeto, el porqué, cuánto oro te falta y qué
    componentes ya te puedes comprar (bajando por la receta si hace falta).
  - **Tu build**: lo que ya tienes marcado y lo siguiente resaltado.
  - **Rivales**, de más a menos peligroso: KDA, súbditos, nivel, tipo de daño, «Tu rival»
    y una etiqueta **Muy fuerte / Fuerte / Normal / Por detrás**.
  - **Tu equipo**, con la misma información.

**Quién va fuerte**: el oro de los rivales no se ve, pero sus objetos sí. Se combina el valor
de sus objetos (60 %), su KDA (25 %) y su nivel (15 %) frente a la media de la partida. En los
primeros 3 minutos no se etiqueta a nadie.

**Qué comprar**: tu build (primer objeto del núcleo, botas, resto) con dos excepciones:
- Si un enemigo que **hace mucho daño** va muy fuerte (o tu rival de línea va fuerte) y no
  tienes defensa contra su tipo de daño, el objeto defensivo de tu tipo de campeón va primero.
- Si alguien que **se cura mucho** va fuerte, la antisanación (su componente barato) va primero.
- Si se dan las dos, manda la que responde al enemigo más fuerte.

Usa la API oficial de Riot de la partida (`127.0.0.1:2999`, verificada con su certificado)
y **solo información visible en el marcador**: está permitido por las normas de Riot.

**Seguridad de la ventana**: el servidor solo escucha en `127.0.0.1`, exige una clave aleatoria
que cambia en cada arranque, comprueba `Host` y `Origin` (ninguna web de tu navegador puede
leerla) y envía cabeceras estrictas sin recursos externos. La ventana usa un perfil de Edge
propio: no se mezcla con tu navegación.

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
uv run lolcoach --demo      # abre la ventana con una partida de ejemplo (para verla sin jugar)
uv run lolcoach --no-window # sin ventana (solo consola)
```

Si cierras la ventana, la consola muestra la dirección para volver a abrirla. Cierra LoL
Coach con **Ctrl+C** (si la consola se cierra de golpe, la ventana antigua se cierra sola la
próxima vez que lo abras).

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
live_poll_interval = 3  # segundos entre lecturas de la partida (1-10)
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
  ui/web/              Fase 4: servidor local seguro, ventana (HTML/CSS/JS) y modo demostración
  ui/                  Presentación (consola; en la fase 4, ventana del segundo monitor)
tests/                 Pruebas automáticas: cliente de LoL simulado y datos reales del parche
```

Cada fase nueva se engancha a los eventos existentes (`PicksFinalized`,
`MyChampionChanged`, `LoadoutApplied`, `BuildApplied`, `ChampSelectEnded`...) sin modificar
lo anterior. Las reglas (`loadout/rules.py`, `builds/rules.py`) son funciones puras: se
pueden ampliar, o sustituir por IA en un plan de pago, sin tocar el resto. La build base
llega por `BuildSource`, preparada para añadir estadísticas reales más adelante.

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
5. Mejoras: builds afinadas con estadísticas reales (API de Riot), IA opcional (de pago),
   historial propio, pulido visual.
