# Estadísticas compartidas

Las builds, runas y hechizos de alto elo salen de partidas reales descargadas con una clave
de Riot. Para que quien no tiene clave (por ejemplo, un amigo) tenga las mismas builds, tu
Erizo Coach puede **publicar** sus estadísticas ya calculadas y el suyo las **descarga**.

## Cómo funciona

- **Qué se publica:** un archivo pequeño (`estadisticas.json.gz`, unos 30 KB) con la build,
  las runas y los hechizos más jugados de cada campeón y posición, y cuántas partidas hay
  detrás. **No lleva ningún nombre, Riot ID, PUUID ni partida concreta**, ni tu clave.
- **Dónde:** en la rama `datos` del propio repositorio de Erizo Coach
  (<https://github.com/eriizzo/ErizoCoach/tree/datos>). Esa rama solo tiene el paquete y un
  README; el código está en `main`. Hasta la 0.23 se publicaba en un repositorio aparte,
  `ErizoCoach-datos`.
- **Cuándo:** tu app lo publica sola tras sus descargas automáticas. Cuando hay un parche
  nuevo, en cuanto tiene 100 partidas; si no, como mucho cada 3 horas. Cada publicación
  sustituye a la anterior: la rama `datos` siempre tiene un solo commit.
- **Quien lo usa:** cualquier Erizo Coach lo descarga como mucho cada 6 horas, lo valida entero
  (tamaño, formato y valores) y guarda una copia por si un día no hay internet. Si además
  tiene estadísticas propias, usa las que tengan datos suficientes del parche más reciente.

## Configurarlo (solo quien publica)

1. En el repositorio, **Settings → Deploy keys → Add deploy key**: pega la clave pública
   (`C:\Users\<usuario>\.ssh\erizocoach_deploy_ed25519.pub`), marca **Allow write access** y
   guarda.
2. **Protege el código** para que la app nunca pueda reescribirlo: **Settings → Rules →
   Rulesets**:
   - una regla de rama para la rama por defecto (`main`) con **Restrict deletions** y
     **Block force pushes**;
   - una regla de etiquetas `v*` con **Restrict updates**, **Restrict deletions** y
     **Block force pushes**.

   La app solo sube a la rama `datos`.
3. Añade a `%LOCALAPPDATA%\ErizoCoach\config.toml`:

   ```toml
   stats_publish_repo = "git@github.com:<usuario>/ErizoCoach.git"
   stats_publish_key = "C:/Users/<usuario>/.ssh/erizocoach_deploy_ed25519"
   ```

4. Para publicar ya, sin esperar, con el cliente de LoL abierto:

   ```bash
   uv run erizocoach stats publicar
   ```

Hace falta tener `git` instalado. Si una publicación falla (sin red, clave rechazada...),
queda en el registro y se vuelve a intentar en la siguiente tanda.

## Para quien no quiere usarlas

`stats_shared = false` en `config.toml` desactiva la descarga: sin clave propia, las builds
salen de las reglas.

## Normas de Riot

Riot permite usar una clave personal en programas para ti y un grupo pequeño y privado. Para
una app pública haría falta registrarla y pedir una clave de producción en
[developer.riotgames.com](https://developer.riotgames.com).
