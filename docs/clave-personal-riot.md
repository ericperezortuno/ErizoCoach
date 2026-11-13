# Clave personal de Riot (sin caducidad diaria)

La clave de desarrollo (*Development API Key*) caduca cada 24 horas. Para no tener que
renovarla, Riot ofrece la **clave personal** (*Personal API Key*) para proyectos de uso propio
como LoL Coach:

- **Mismos límites** que la de desarrollo: 20 peticiones por segundo y 100 cada 2 minutos.
  A LoL Coach le sobran (descarga unas 1.500 partidas por hora).
- **Solo para ti**: Riot no permite usarla en aplicaciones públicas o para otros usuarios.
- **Gratis**. La pides con un formulario y Riot la revisa a mano; no indica cuánto tarda.
- La web de Riot no dice de forma explícita si caduca. La comunidad indica que no caduca
  cada día como la de desarrollo; cuando te la den, el portal muestra su fecha de caducidad.

## Cómo pedirla

1. Entra en [developer.riotgames.com](https://developer.riotgames.com) con tu cuenta de Riot.
2. Pulsa **REGISTER PRODUCT** y elige **PERSONAL API KEY**.
3. Acepta las políticas y rellena el formulario. Puedes copiar estos textos (Riot revisa en
   inglés):

   **Product Name**

   ```text
   LoL Coach (personal)
   ```

   **Product Description**

   ```text
   LoL Coach is a personal, non-commercial desktop tool that I use only on my own Windows PC
   while playing League of Legends on EUW. It is not distributed or offered to anyone else.

   What it does:
   - Uses the official local League Client API (LCU) to set my own rune page, summoner spells
     and an in-shop item set at the end of champion select.
   - Uses the official Live Client Data API during my own games to show scoreboard-based tips
     (who is ahead, what to buy next) on my second monitor. No memory reading, no injection,
     no input automation and no information that is not already visible in the game.

   How it uses the Riot API:
   - league-v4: Challenger, Grandmaster and Master entries for EUW1, to find high-elo players.
   - match-v5: their recent ranked solo matches (match details and timelines) for the current
     patch.
   - From each match it keeps only champion, position, win/loss, item purchase order, runes and
     summoner spells, to compute the most common build, runes and spells per champion and
     position. It does not keep names or Riot IDs; PUUIDs stay in a local queue only to know
     whose matches to request. Nothing leaves my PC and nothing is shown to other people.

   Rate limits: it follows the X-App-Rate-Limit headers (20/1s, 100/2min), honours 429
   Retry-After, pauses while I am in champion select or in a game, and stops at about 3,000
   matches per patch.

   The key is stored in Windows Credential Manager, never in code, files or logs.
   ```

   **Product URL**: si te lo pide, puedes poner el repositorio de GitHub (solo si es público)
   o dejarlo vacío.

   **Product Game Focus**: League of Legends.

4. Envía el formulario. Cuando Riot lo apruebe, verás la clave nueva en tu panel, en
   *Apps* (la de desarrollo sigue funcionando mientras tanto).

## Cuando te la den

Guárdala igual que la de desarrollo. Sustituye a la anterior:

```bash
lolcoach-consola stats clave
```

Desde el código fuente: `uv run lolcoach stats clave`. LoL Coach la usará en la siguiente
descarga automática; no hace falta cambiar nada más.
