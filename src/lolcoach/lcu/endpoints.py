"""Rutas de la API del cliente que usa la aplicación (todas en 127.0.0.1)."""

# Estado y sesión
GAMEFLOW_PHASE = "/lol-gameflow/v1/gameflow-phase"
GAMEFLOW_SESSION = "/lol-gameflow/v1/session"
CHAMP_SELECT_SESSION = "/lol-champ-select/v1/session"
CHAMP_SELECT_MY_SELECTION = "/lol-champ-select/v1/session/my-selection"  # hechizos
SYSTEM_BUILDS = "/system/v1/builds"
REGION_LOCALE = "/riotclient/region-locale"

# Datos del juego (los trae el propio cliente, en tu idioma y de tu parche)
CHAMPION_SUMMARY = "/lol-game-data/assets/v1/champion-summary.json"
CHAMPION_DETAILS = "/lol-game-data/assets/v1/champions/{champion_id}.json"
PERKS = "/lol-game-data/assets/v1/perks.json"
PERK_STYLES = "/lol-game-data/assets/v1/perkstyles.json"
SUMMONER_SPELLS = "/lol-game-data/assets/v1/summoner-spells.json"
ITEMS = "/lol-game-data/assets/v1/items.json"

# Cuenta y conjuntos de objetos (los que aparecen en la tienda de la partida)
CURRENT_SUMMONER = "/lol-summoner/v1/current-summoner"
ITEM_SETS = "/lol-item-sets/v1/item-sets/{summoner_id}/sets"

# Runas
PERK_PAGES = "/lol-perks/v1/pages"
PERK_PAGE = "/lol-perks/v1/pages/{page_id}"
PERK_CURRENT_PAGE = "/lol-perks/v1/currentpage"
PERK_INVENTORY = "/lol-perks/v1/inventory"
RECOMMENDED_POSITIONS = "/lol-perks/v1/recommended-champion-positions"
RECOMMENDED_PAGES = (
    "/lol-perks/v1/recommended-pages/champion/{champion_id}/position/{position}/map/{map_id}"
)
