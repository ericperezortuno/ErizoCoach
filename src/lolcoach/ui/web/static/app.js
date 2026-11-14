"use strict";

// La ventana solo pinta el estado que le envía LoL Coach. Funciona de dos formas:
// * Escritorio (pywebview): puente directo con la app, sin servidor ni red.
// * Navegador (respaldo): servidor local con websocket.
// Todo el texto se inserta con textContent (nunca como HTML).

const DESKTOP = document.documentElement.dataset.mode === "desktop";
const token = new URLSearchParams(location.search).get("t") || "";
const app = document.getElementById("app");
const iconCache = new Map(); // escritorio: clave -> imagen incrustada (o promesa)
let retries = 0;
let socket = null;    // navegador: canal con la app (también para cambiar las runas)
let lastState = null; // para repintar al tocar el editor de runas sin esperar a la app

// La app llama a window.lolcoach.render(estado) cuando algo cambia (escritorio).
window.lolcoach = { render: (state) => render(state) };

function startDesktop() {
  window.addEventListener("pywebviewready", async () => {
    setConnection(true);
    setupBackground();
    render(await window.pywebview.api.get_state());
  });
}

// Botón «Segundo plano»: la app esconde la ventana y sigue trabajando (solo escritorio;
// en el navegador no hay icono en la bandeja con el que volver).
function setupBackground() {
  const button = document.getElementById("to-background");
  if (!button || !window.pywebview.api.background) return;
  button.hidden = false;
  button.addEventListener("click", async () => {
    button.disabled = true;
    let ok = false;
    try { ok = await window.pywebview.api.background(); } catch { ok = false; }
    if (ok) {
      button.disabled = false;
      button.blur(); // al volver, que no se quede marcado
    } else {
      button.title = "No disponible: Windows no ha dejado poner el icono de LoL Coach junto al reloj.";
    }
  });
}

function setIcon(img, kind, id) {
  if (!DESKTOP) {
    img.src = `/icon/${kind}/${id}.png`;
    return;
  }
  const key = `${kind}:${id}`;
  const cached = iconCache.get(key);
  if (typeof cached === "string") {
    if (cached) img.src = cached;
    return;
  }
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const pending = cached || api.icon(kind, id)
    .then((src) => { iconCache.set(key, src || ""); return src || ""; })
    .catch(() => { iconCache.delete(key); return ""; });
  iconCache.set(key, pending);
  pending.then((src) => { if (src) img.src = src; });
}

function connect() {
  socket = new WebSocket(`ws://${location.host}/ws?t=${encodeURIComponent(token)}`);
  socket.onopen = () => { retries = 0; setConnection(true); };
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") render(message.state);
  };
  socket.onclose = () => {
    setConnection(false);
    setTimeout(connect, Math.min(1000 * 2 ** retries++, 10000));
  };
}

function setConnection(on) {
  document.getElementById("connection").className = on ? "dot on" : "dot off";
  setText("connection-text", on ? "Conectado a LoL Coach" : "Sin conexión con LoL Coach (¿está abierto?)");
}

// --- Movimiento ----------------------------------------------------------------------
// La ventana se repinta entera en cada actualización (cada ~3 s en partida), así que solo
// se anima lo que CAMBIA respecto a lo pintado antes: la entrada al cambiar de pantalla,
// el oro, la compra recomendada, lo que acabas de comprar y los jugadores que cambian de
// estado. Siempre transform/opacity (las compone la GPU) y nada en bucle.
// Con «reducir movimiento» de Windows, los desplazamientos y rebotes pasan a ser fundidos
// cortos (no se quita todo: así nada aparece de golpe).

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const EASE_OUT = "cubic-bezier(.16, 1, .3, 1)";   // llegadas suaves
const EASE_POP = "cubic-bezier(.34, 1.56, .64, 1)"; // un rebote discreto

let memory = { view: null, phase: null, gold: null, buyId: null, canBuy: false, progress: 0, owned: new Set(), players: new Map() };
let seen = memory; // lo que se está pintando ahora; al terminar pasa a ser `memory`
let queue = [];    // animaciones que se lanzan cuando el DOM nuevo ya está en la página

function motion(fn) { queue.push(fn); }

function animate(node, keyframes, options) {
  if (!node || !node.animate) return null;
  try { return node.animate(keyframes, options); } catch { return null; }
}

// Animación completa, o un fundido de ≤150 ms si el usuario pidió reducir el movimiento.
function move(node, keyframes, options) {
  if (!reducedMotion.matches) return animate(node, keyframes, options);
  const hasFade = keyframes.some((frame) => "opacity" in frame);
  if (!hasFade || options.pseudoElement) return null;
  const opacities = keyframes.map((frame) => ({ opacity: frame.opacity ?? 1 }));
  return animate(node, opacities, { duration: 150, delay: Math.min(options.delay || 0, 150), easing: "ease-out", fill: options.fill });
}

function countUp(node, from, to, duration = 650) {
  if (reducedMotion.matches || from === to) { node.textContent = String(to); return; }
  const start = performance.now();
  node.textContent = String(from);
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    node.textContent = String(Math.round(from + (to - from) * (1 - (1 - t) ** 3)));
    if (t < 1 && node.isConnected) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// Al cambiar de pantalla: las tarjetas entran escalonadas y su contenido les sigue.
function playEntrance() {
  [...app.children].forEach((card, index) => {
    const delay = index * 80;
    move(card, [{ opacity: 0, transform: "translateY(16px) scale(.98)" }, { opacity: 1, transform: "none" }],
      { duration: 560, delay, easing: EASE_OUT, fill: "backwards" });
    const parts = card.querySelectorAll(".player, .rune-page .tree, .spells, .icons > .icon, .situational .entry, .buy-body, .components, .hero > div, .empty > :not(.emblem)");
    parts.forEach((part, order) => move(part, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }],
      { duration: 420, delay: delay + 140 + Math.min(order, 10) * 35, easing: EASE_OUT, fill: "backwards" }));
  });
}

// --- Utilidades de DOM -----------------------------------------------------------

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Cambia un texto solo si es distinto (así los lectores de pantalla no lo repiten).
function setText(id, text) {
  const node = document.getElementById(id);
  if (node && node.textContent !== text) node.textContent = text;
}

function classes(...names) {
  return names.filter(Boolean).join(" ");
}

function itemIcon(item, { big = false, owned = false, next = false, count = 1 } = {}) {
  if (!item) return null;
  const price = item.price ? ` (${item.price} de oro)` : "";
  const status = owned ? ", ya comprado" : next ? ", siguiente compra" : "";
  const img = el("img", { alt: owned ? `${item.name} (comprado)` : item.name });
  setIcon(img, "item", item.id);
  return el("span", { class: classes("icon", big && "big", owned && "owned", next && "next"), title: item.name + price + status }, [
    img,
    count > 1 ? el("span", { class: "count", text: `x${count}` }) : null,
  ]);
}

function championIcon(id, name, { big = false } = {}) {
  const className = classes("icon", "champ", big && "big");
  if (!id) return el("span", { class: className });
  const img = el("img", { alt: name });
  setIcon(img, "champion", id);
  return el("span", { class: className, title: name }, [img]);
}

// Tarjeta de vidrio con título y, opcionalmente, un dato a la derecha del título.
function card(title, children, { className = "", extra = null } = {}) {
  return el("section", { class: classes("card", className) }, [
    el("div", { class: "card-head" }, [el("h2", { text: title }), extra]),
    ...children,
  ]);
}

function goldText(diff) {
  const abs = Math.abs(diff);
  const text = abs >= 1000 ? `${(abs / 1000).toFixed(1).replace(".", ",")}k` : `${abs}`;
  return `${diff >= 0 ? "+" : "−"}${text}`;
}

function bulletList(items) {
  return el("ul", { class: "list" }, items.map((text) => el("li", { text })));
}

function originLine(source, fallback) {
  const text = source && source.startsWith("estadísticas") ? `Base: ${source}` : `Base: ${fallback}`;
  return el("div", { class: "muted small origin", text });
}

// --- Vistas ------------------------------------------------------------------------

function viewOf(state) {
  if (state.game) return "game";
  if (state.champion || state.loadout || state.build) return "champ";
  return state.connected ? "menu" : "offline";
}

// `boot`: el estado provisional del arranque se pinta sin animar; la entrada la hace el primero real.
function render(state, boot = false) {
  lastState = state;
  const view = viewOf(state);
  const phase = state.phaseLabel || "";
  seen = { view: boot ? null : view, entering: !boot && view !== memory.view, phase, gold: null, buyId: null, canBuy: false, progress: 0, owned: new Set(), players: new Map() };
  queue = [];

  setText("phase", phase);
  document.getElementById("phase").title = phase; // por si no cabe entera
  setText("clock", state.game ? state.game.time : "");
  setText("version", state.version ? `v${state.version}` : "");
  if (!boot && memory.phase !== null && memory.phase !== phase) {
    motion(() => move(document.getElementById("phase"), [{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "none" }],
      { duration: 260, easing: EASE_OUT }));
  }

  let sections;
  if (view === "game") sections = renderGame(state);
  else if (view === "champ") sections = renderChampSelect(state);
  else sections = [renderIdle(state)];
  if (state.stats && state.stats.problem) {
    sections.unshift(card("Aviso", [el("div", { text: state.stats.text })], { className: "notice wide" }));
  }
  // Se repinta todo: el botón que tenías enfocado (teclado) recupera el foco.
  const focused = document.activeElement && document.activeElement.dataset
    ? document.activeElement.dataset.focusKey : null;
  app.replaceChildren(...sections.filter(Boolean));
  if (focused) {
    const again = app.querySelector(`[data-focus-key="${CSS.escape(focused)}"]`);
    if (again) again.focus({ preventScroll: true });
  }

  if (seen.entering) motion(playEntrance);
  for (const run of queue) run();
  memory = seen;
}

function renderIdle(state) {
  const choosing = state.connected && state.phase === "ChampSelect";
  const title = choosing ? "Selección de campeones"
    : state.connected ? "Esperando a la selección de campeones" : "Esperando al cliente de LoL";
  const hint = choosing
    ? "Preselecciona o mira un campeón y verás aquí sus runas y hechizos. Se ponen solos al hacerse el último pick."
    : state.connected
      ? "En la selección verás las runas y hechizos del campeón que preselecciones; al último pick se ponen solos, con tu build. Durante la partida, qué comprar y quién va fuerte."
      : "Abre el cliente de League of Legends: LoL Coach se conectará solo.";
  const stats = state.stats && !state.stats.problem ? el("div", { class: "small stats", text: state.stats.text }) : null;
  const emblem = el("span", { class: "emblem", "aria-hidden": "true" });
  if (seen.entering) {
    // El emblema se «arma» una vez: el anillo gira un cuarto de vuelta y el rombo aparece.
    motion(() => {
      move(emblem, [{ transform: "rotate(-90deg)" }, { transform: "none" }], { duration: 1400, easing: EASE_OUT, pseudoElement: "::before" });
      move(emblem, [{ opacity: 0, transform: "rotate(45deg) scale(.4)" }, { opacity: 1, transform: "rotate(45deg) scale(1)" }],
        { duration: 700, delay: 200, easing: EASE_POP, fill: "backwards", pseudoElement: "::after" });
    });
  }
  return el("section", { class: "card empty wide" }, [
    emblem,
    el("h2", { class: "big", text: title }),
    el("div", { class: "hint", text: hint }),
    stats,
  ]);
}

function renderChampSelect(state) {
  return [
    state.champion ? renderHero(state) : null,
    state.loadout ? renderLoadout(state.loadout, state) : null,
    state.build ? renderBuild(state.build) : null,
  ];
}

function renderHero(state) {
  const champion = state.champion;
  const archetype = state.build && state.build.archetype;
  const portrait = championIcon(champion.id, champion.name, { big: true });
  if (seen.entering) {
    motion(() => move(portrait, [{ opacity: 0, transform: "scale(.7) rotate(-8deg)" }, { opacity: 1, transform: "none" }],
      { duration: 700, delay: 120, easing: EASE_POP, fill: "backwards" }));
  }
  return el("section", { class: "card lens hero wide" }, [
    portrait,
    el("div", {}, [
      el("h2", { class: "name", text: champion.name }),
      el("div", { class: "meta" }, [
        champion.position ? el("span", { class: "tag", text: champion.position }) : null,
        champion.preview ? el("span", { class: "tag preview", text: "Previsión" }) : null,
        archetype ? el("span", { text: archetype }) : null,
      ]),
    ]),
  ]);
}

// --- Runas: la página con sus imágenes y el editor ------------------------------------
// Al tocar una runa en el editor, la página se cambia aquí al momento (borrador) y se envía
// a la app, que la valida y la escribe en tu página «LoL Coach» del cliente. El borrador se
// enseña hasta que la app confirma (o 4 s, si algo falla: entonces vuelve lo que hay).

const SEND_DELAY = 350; // varios clics seguidos = un solo envío
const runeEditor = { open: false, pending: null, until: 0, timer: 0, problem: "" };

function gameIcon(kind, id, name, className) {
  const img = el("img", { alt: name || "" });
  if (id) setIcon(img, kind, id);
  return el("span", { class: classes("rune", className), title: name || "" }, [img]);
}

function samePerks(a, b) {
  return a.primaryStyleId === b.primaryStyleId && a.subStyleId === b.subStyleId
    && a.perkIds.length === b.perkIds.length && a.perkIds.every((id, i) => id === b.perkIds[i]);
}

function pageFromState(page) {
  return { primaryStyleId: page.primaryStyle.id, subStyleId: page.subStyle.id, perkIds: page.perkIds.slice() };
}

// La página que se enseña: tu borrador mientras la app lo pone; si no, la del estado.
function shownPage(loadout) {
  if (!loadout.page) return null;
  const real = pageFromState(loadout.page);
  const draft = runeEditor.pending;
  const failed = loadout.problem && loadout.problem !== runeEditor.problem;
  if (draft && (samePerks(draft, real) || Date.now() > runeEditor.until || failed)) {
    runeEditor.pending = null;
  }
  return runeEditor.pending || real;
}

function renderLoadout(loadout, state) {
  const catalog = state.runeCatalog;
  const page = shownPage(loadout);
  const names = catalog ? catalog.names : {};
  const name = (id) => names[id] || "";
  const styleName = (id) => {
    const style = catalog && catalog.styles.find((s) => s.id === id);
    return style ? style.name : "";
  };
  const children = [
    loadout.status ? el("div", { class: classes("small", "status-line", loadout.runesBy && "applied"), text: loadout.status }) : null,
    loadout.problem ? el("div", { class: "small problem", role: "alert", text: loadout.problem }) : null,
    originLine(loadout.source, "recomendación de Riot (aún sin datos suficientes de partidas reales)"),
  ];
  if (page && loadout.page) {
    const perks = page.perkIds;
    const fallback = loadout.page; // nombres por si aún no hay catálogo
    const perkName = (index, fromState) => name(perks[index]) || fromState.name;
    children.push(el("div", { class: "rune-page" }, [
      el("div", { class: "tree" }, [
        gameIcon("style", page.primaryStyleId, styleName(page.primaryStyleId) || fallback.primaryStyle.name, "style"),
        gameIcon("perk", perks[0], perkName(0, fallback.keystone), "keystone"),
        ...[1, 2, 3].map((i) => gameIcon("perk", perks[i], perkName(i, fallback.primary[i - 1] || {}), "minor")),
      ]),
      el("div", { class: "tree" }, [
        gameIcon("style", page.subStyleId, styleName(page.subStyleId) || fallback.subStyle.name, "style"),
        ...[4, 5].map((i) => gameIcon("perk", perks[i], perkName(i, fallback.secondary[i - 4] || {}), "minor")),
        el("span", { class: "shards" }, [6, 7, 8].map((i) => gameIcon("perk", perks[i], perkName(i, fallback.shards[i - 6] || {}), "shard"))),
      ]),
      el("div", { class: "keystone-name", text: perkName(0, fallback.keystone) }),
    ]));
  }
  if (loadout.spells.length) {
    children.push(el("div", { class: "spells" }, [
      el("span", { class: "section-label inline", text: "Hechizos" }),
      ...loadout.spells.map((s) => gameIcon("spell", s.id, s.name, "spell")),
    ]));
  }
  if (loadout.laneOpponent) children.push(el("div", { class: "muted small", text: `Rival de línea probable: ${loadout.laneOpponent}` }));
  const canEdit = Boolean(loadout.editable && catalog && page && state.connected && state.phase === "ChampSelect");
  if (loadout.options && loadout.options.length > 1) children.push(renderRuneOptions(loadout.options, page, canEdit));
  if (canEdit) {
    const toggle = el("button", {
      class: "ghost edit-toggle", type: "button", "data-focus-key": "edit-toggle",
      "aria-expanded": runeEditor.open ? "true" : "false",
      text: runeEditor.open ? "Cerrar el editor" : "Cambiar runas",
    });
    toggle.addEventListener("click", () => { runeEditor.open = !runeEditor.open; render(lastState); });
    children.push(toggle);
    if (runeEditor.open) children.push(renderRuneEditor(page, catalog));
  } else if (!loadout.editable || state.phase !== "ChampSelect") {
    runeEditor.open = false;
  }
  const extra = [...loadout.adjustments, ...loadout.notes];
  if (extra.length) children.push(bulletList(extra));
  return card(loadout.preview && !loadout.runesBy ? "Runas y hechizos (previsión)" : "Runas y hechizos", children,
    { className: "loadout" });
}

// Hasta 3 páginas con piedras angulares distintas: tocar una la pone en el cliente.
function renderRuneOptions(options, page, canEdit) {
  const buttons = options.map((option, index) => {
    const choice = pageFromState(option.page);
    const selected = Boolean(page && samePerks(choice, page));
    const keystone = option.page.keystone;
    const button = el("button", {
      type: "button", class: classes("rune-choice", selected && "selected"),
      "aria-pressed": selected ? "true" : "false", disabled: canEdit ? null : "true",
      "data-focus-key": `option:${index}`,
      title: canEdit ? `Poner ${keystone.name} en el cliente` : keystone.name,
    }, [
      gameIcon("perk", keystone.id, "", "choice-keystone"),
      el("span", { class: "choice-text" }, [
        el("span", { class: "choice-name", text: keystone.name }),
        el("span", { class: "choice-detail", text: option.detail }),
      ]),
    ]);
    if (canEdit) button.addEventListener("click", () => { if (!selected) sendRunes(choice); });
    return button;
  });
  return el("div", { class: "rune-choices" }, [
    el("div", { class: "section-label", text: canEdit ? "Elige una de estas páginas" : "Otras páginas posibles" }),
    el("div", { class: "choice-row", role: "group", "aria-label": "Páginas de runas para elegir" }, buttons),
  ]);
}

function styleOf(catalog, id) {
  return catalog.styles.find((s) => s.id === id);
}

function rowIn(style, perkId) {
  return style ? style.rows.findIndex((row) => row.includes(perkId)) : -1;
}

// Cambios del editor: siempre devuelven una página válida (como hace el cliente).
function withPrimary(catalog, page, styleId) {
  const style = styleOf(catalog, styleId);
  if (!style || styleId === page.primaryStyleId) return page;
  let sub = page.subStyleId;
  const allowed = (id) => id !== styleId && styleOf(catalog, id) && (!style.allowedSub.length || style.allowedSub.includes(id));
  if (!allowed(sub)) sub = (catalog.styles.find((s) => allowed(s.id)) || {}).id;
  const subStyle = styleOf(catalog, sub);
  if (!subStyle) return page;
  const secondary = sub === page.subStyleId ? page.perkIds.slice(4, 6) : [subStyle.rows[1][0], subStyle.rows[2][0]];
  return { primaryStyleId: styleId, subStyleId: sub, perkIds: [...style.rows.map((row) => row[0]), ...secondary, ...page.perkIds.slice(6)] };
}

function withSub(catalog, page, styleId) {
  const style = styleOf(catalog, styleId);
  if (!style || styleId === page.subStyleId || styleId === page.primaryStyleId) return page;
  const perks = page.perkIds.slice();
  perks[4] = style.rows[1][0];
  perks[5] = style.rows[2][0];
  return { ...page, subStyleId: styleId, perkIds: perks };
}

function withPrimaryRune(page, row, perkId) {
  const perks = page.perkIds.slice();
  perks[row] = perkId; // fila 0 = piedra angular (índice 0); filas 1-3 = índices 1-3
  return { ...page, perkIds: perks };
}

function withSecondaryRune(catalog, page, perkId) {
  const style = styleOf(catalog, page.subStyleId);
  const picks = page.perkIds.slice(4, 6);
  if (picks.includes(perkId)) return page;
  const row = rowIn(style, perkId);
  const sameRow = picks.findIndex((id) => rowIn(style, id) === row);
  if (sameRow >= 0) picks[sameRow] = perkId;
  else { picks.shift(); picks.push(perkId); } // como el cliente: se va la más antigua
  const perks = page.perkIds.slice();
  perks.splice(4, 2, ...picks);
  return { ...page, perkIds: perks };
}

function withShard(page, row, perkId) {
  const perks = page.perkIds.slice();
  perks[6 + row] = perkId;
  return { ...page, perkIds: perks };
}

function sendRunes(page) {
  runeEditor.pending = page;
  runeEditor.problem = (lastState && lastState.loadout && lastState.loadout.problem) || "";
  runeEditor.until = Date.now() + 4000;
  clearTimeout(runeEditor.timer);
  runeEditor.timer = setTimeout(() => {
    const draft = runeEditor.pending;
    if (!draft) return;
    runeEditor.until = Date.now() + 4000;
    if (DESKTOP) {
      const api = window.pywebview && window.pywebview.api;
      if (api && api.set_runes) api.set_runes(draft).catch(() => {});
    } else if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "setRunes", page: draft }));
    }
  }, SEND_DELAY);
  render(lastState);
}

function optionButton(kind, id, label, selected, onPick, size, group) {
  const button = el("button", {
    type: "button", class: classes("rune-option", size, selected && "selected"),
    "aria-pressed": selected ? "true" : "false", "aria-label": label, title: label,
    "data-focus-key": `${group}|${id}`,
  }, [gameIcon(kind, id, "", "")]);
  button.addEventListener("click", () => { if (!selected) onPick(); });
  return button;
}

function optionRow(label, options) {
  // options: [kind, id, etiqueta, elegida, al elegir, tamaño]
  return el("div", { class: "editor-row", role: "group", "aria-label": label },
    options.map((o) => optionButton(...o, label)));
}

function renderRuneEditor(page, catalog) {
  const name = (id) => catalog.names[id] || `Runa ${id}`;
  const primary = styleOf(catalog, page.primaryStyleId);
  const sub = styleOf(catalog, page.subStyleId);
  if (!primary || !sub) return null;
  const perks = page.perkIds;
  const pick = (next) => sendRunes(next);
  const rowLabels = ["Piedra angular", "Fila 1", "Fila 2", "Fila 3"];

  const primaryTrees = optionRow("Árbol principal", catalog.styles.map((s) =>
    ["style", s.id, `Árbol principal: ${s.name}`, s.id === primary.id, () => pick(withPrimary(catalog, page, s.id)), "tree-option"]));
  const primaryRows = primary.rows.map((row, r) => optionRow(`${primary.name}: ${rowLabels[r]}`, row.map((id) =>
    ["perk", id, name(id), perks[r] === id, () => pick(withPrimaryRune(page, r, id)), r === 0 ? "keystone-option" : "minor-option"])));
  const subTrees = optionRow("Árbol secundario", catalog.styles.filter((s) => s.id !== primary.id).map((s) =>
    ["style", s.id, `Árbol secundario: ${s.name}`, s.id === sub.id, () => pick(withSub(catalog, page, s.id)), "tree-option"]));
  const subRows = sub.rows.slice(1).map((row, r) => optionRow(`${sub.name}: ${rowLabels[r + 1]}`, row.map((id) =>
    ["perk", id, name(id), perks[4] === id || perks[5] === id, () => pick(withSecondaryRune(catalog, page, id)), "minor-option"])));
  const shardRows = catalog.shards.map((row, r) => optionRow(`Fragmentos: fila ${r + 1}`, row.map((id) =>
    ["perk", id, name(id), perks[6 + r] === id, () => pick(withShard(page, r, id)), "shard-option"])));

  return el("div", { class: "rune-editor" }, [
    el("div", { class: "muted small", text: "Toca una runa: se cambia al momento en tu página «LoL Coach» del cliente." }),
    el("div", { class: "editor-columns" }, [
      el("div", { class: "editor-tree" }, [el("div", { class: "section-label", text: "Principal" }), primaryTrees, ...primaryRows]),
      el("div", { class: "editor-tree" }, [
        el("div", { class: "section-label", text: "Secundario (elige 2)" }), subTrees, ...subRows,
        el("div", { class: "section-label", text: "Fragmentos" }), ...shardRows,
      ]),
    ]),
  ]);
}

function renderBuild(build) {
  const children = [
    originLine(build.source, "reglas por tipo de campeón (aún sin datos suficientes de partidas reales)"),
    el("div", { class: "section-label", text: "Inicio" }),
    el("div", { class: "icons" }, build.starting.map((i) => itemIcon(i, { count: i.count }))),
    el("div", { class: "section-label", text: "Núcleo, en orden" }),
    el("div", { class: "icons" }, build.core.flatMap((item, index) => [
      index ? el("span", { class: "arrow", "aria-hidden": "true", text: "›" }) : null, itemIcon(item),
    ])),
    el("div", { class: "section-label", text: "Build completa" }),
    el("div", { class: "icons" }, build.full.map((i) => itemIcon(i))),
  ];
  if (build.situational.length) {
    children.push(el("div", { class: "section-label", text: "Contra este equipo" }));
    children.push(el("div", { class: "situational" }, build.situational.map((s) =>
      el("div", { class: "entry" }, [itemIcon(s), el("span", { class: "small", text: s.reason })]))));
  }
  if (build.notes.length) children.push(bulletList(build.notes));
  return card("Build", children); // el arquetipo ya se ve junto a tu campeón
}

function renderGame(state) {
  const game = state.game;
  const team = game.team || [game.me, ...game.allies].filter(Boolean);
  return [
    renderPurchase(game),
    game.buildProgress.length ? renderProgress(game) : null,
    // Los dos equipos van por línea (superior → apoyo): cada fila frente a su rival.
    // Tu equipo a la izquierda y los rivales a la derecha.
    team.length ? card("Tu equipo", [renderPlayers(team, game.me, "ally")]) : null,
    game.enemies.length ? card("Rivales", [renderPlayers(game.enemies, null, "enemy")]) : null,
  ];
}

function renderCoins(gold) {
  const before = memory.gold;
  seen.gold = gold;
  const amount = el("span", { class: "amount", text: String(gold) });
  const coins = el("span", { class: "coins" }, [amount, el("small", { text: "de oro" })]);
  if (!seen.entering && before !== null && before !== gold) {
    motion(() => {
      countUp(amount, before, gold);
      if (gold - before >= 150) {
        move(coins, [{ transform: "scale(1)" }, { transform: "scale(1.45)" }, { transform: "scale(1)" }],
          { duration: 520, easing: EASE_OUT, pseudoElement: "::before" });
      }
    });
  }
  return coins;
}

function renderPurchase(game) {
  const next = game.next;
  const coins = renderCoins(game.gold);
  if (!next) {
    return card("Compra ahora", [el("div", { class: "muted", text: game.message || "Sin recomendación de compra en este modo." })],
      { className: "lens wide", extra: coins });
  }
  const sameItem = memory.buyId === next.id;
  const newItem = !sameItem && !seen.entering;
  const becameReady = sameItem && next.canBuy && !memory.canBuy;
  const progress = next.canBuy ? 1 : next.price > 0 ? Math.min(1, Math.max(0, 1 - next.remaining / next.price)) : 0;
  seen.buyId = next.id;
  seen.canBuy = next.canBuy;
  seen.progress = progress;
  if (!sameItem || becameReady) {
    // Solo se anuncia lo que cambia de verdad, no cada actualización del oro.
    setText("announce", `Compra ahora: ${next.name}. ${next.canBuy ? "Te llega el oro." : `Te faltan ${next.remaining} de oro.`}`);
  }

  // Objetos iniciales: el principal en grande y lo demás (pociones...) al lado, con su cantidad.
  const icon = next.starting && next.starting.length > 1
    ? el("span", { class: "starting-icons" },
      next.starting.map((s, i) => itemIcon(s, { big: i === 0, count: s.count })))
    : itemIcon(next, { big: true });
  const name = el("div", { class: "name", text: next.name });
  const verdict = next.canBuy
    ? el("div", { class: "verdict ok", text: "Te llega el oro: ¡cómpralo!" })
    : el("div", { class: "verdict warn", text: `Te faltan ${next.remaining} de oro` });
  const fill = el("span", { class: "fill" });
  fill.style.setProperty("--p", progress.toFixed(3));
  const halo = el("span", { class: "halo", "aria-hidden": "true" });
  const components = next.components.length
    ? el("div", { class: "components" }, [el("span", { class: "small muted", text: "Ya puedes comprar:" }),
      ...next.components.map((c) => itemIcon(c))])
    : null;

  // El oro líquido sube desde donde estaba (o desde vacío si el objeto es nuevo).
  const from = seen.entering || !sameItem ? 0 : memory.progress;
  if (from !== progress) {
    const level = (p) => `translateX(${(Math.max(p, 0.03) - 1) * 100}%)`;
    motion(() => move(fill, [{ transform: level(from) }, { transform: level(progress) }],
      { duration: seen.entering ? 900 : 650, delay: seen.entering ? 320 : 0, easing: EASE_OUT, fill: "backwards" }));
  }
  if (newItem) {
    motion(() => {
      move(icon, [{ opacity: 0, transform: "scale(.6) rotate(-8deg)" }, { opacity: 1, transform: "none" }], { duration: 560, easing: EASE_POP });
      move(name, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 380, delay: 80, easing: EASE_OUT, fill: "backwards" });
    });
  }
  if (next.canBuy && (newItem || becameReady)) {
    motion(() => {
      move(verdict, [{ opacity: 0.4, transform: "scale(.9)" }, { opacity: 1, transform: "scale(1.05)" }, { opacity: 1, transform: "none" }],
        { duration: 520, delay: 120, easing: EASE_OUT, fill: "backwards" });
      // Dos latidos en 1,7 s (lejos de los 3 destellos/s de WCAG); con movimiento reducido, uno solo.
      if (reducedMotion.matches) animate(halo, [{ opacity: 0 }, { opacity: 0.9 }, { opacity: 0 }], { duration: 700, easing: "ease-in-out" });
      else animate(halo, [{ opacity: 0 }, { opacity: 1 }, { opacity: 0.15 }, { opacity: 0.85 }, { opacity: 0 }], { duration: 1700, easing: "ease-in-out" });
    });
  }

  const section = card("Compra ahora", [
    el("div", { class: "buy" }, [
      icon,
      el("div", { class: "buy-body" }, [
        name,
        verdict,
        el("div", { class: "meter", "aria-hidden": "true" }, [fill]),
        el("div", { class: "reason", text: next.reason }),
      ]),
    ]),
    components,
  ], { className: classes("lens", "wide", next.canBuy && "ready", (newItem || becameReady) && "fresh"), extra: coins });
  section.append(halo);
  return section;
}

function renderProgress(game) {
  const nextId = game.next ? game.next.id : null;
  const owned = game.buildProgress.filter((item) => item.owned).length;
  const icons = game.buildProgress.map((item) => {
    const icon = itemIcon(item, { owned: item.owned, next: item.id === nextId });
    if (!item.owned) return icon;
    seen.owned.add(item.id);
    if (!seen.entering && memory.view === "game" && !memory.owned.has(item.id)) {
      // Recién comprado: el icono se apaga con suavidad y le salta el ✓.
      motion(() => {
        move(icon.firstChild, [{ opacity: 1, filter: "saturate(1)", transform: "scale(1.18)" }, { opacity: 0.42, filter: "saturate(.35)", transform: "none" }],
          { duration: 700, easing: EASE_OUT });
        move(icon, [{ transform: "scale(0)" }, { transform: "scale(1)" }], { duration: 480, delay: 160, easing: EASE_POP, fill: "backwards", pseudoElement: "::after" });
      });
    }
    return icon;
  });
  return card("Tu build", [el("div", { class: "icons" }, icons)], {
    className: "wide",
    extra: el("span", { class: "extra", text: `${owned} de ${game.buildProgress.length} comprados` }),
  });
}

function renderPlayers(players, me, side) {
  return el("ul", { class: "players" }, players.map((p) => {
    const tags = [
      p.me || p === me ? el("span", { class: "tag", text: "Tú" }) : null,
      p.lane ? el("span", { class: "tag lane", text: "Tu rival" }) : null,
      p.damage === "AP" ? el("span", { class: "tag dmg-ap", text: "AP" }) : null,
      p.damage === "AD" ? el("span", { class: "tag dmg-ad", text: "AD" }) : null,
    ];
    const stats = el("div", { class: "stats" }, [
      el("span", { class: "kda", text: p.kda }),
      el("span", { text: `${p.cs} CS` }),
      el("span", { text: `Nv ${p.level}` }),
      p.dead && p.respawn ? el("span", { class: "respawn", text: `reaparece en ${p.respawn} s` }) : null,
    ]);
    // Frente a su rival de posición; si no se sabe quién es (otro modo), frente a la media.
    const vsLane = p.laneGoldDiff !== null && p.laneGoldDiff !== undefined;
    const diff = vsLane ? p.laneGoldDiff : p.goldDiff;
    const goldClass = diff > 400 ? "gold up" : diff < -400 ? "gold down" : "gold";
    const goldTitle = vsLane
      ? `Oro en objetos frente a ${p.laneRival}, su rival de posición`
      : "Oro en objetos frente a la media de la partida";
    const badge = el("span", { class: `badge ${p.strength}`, text: p.strengthLabel });
    const row = el("li", { class: classes("player", p.strength, p.dead && "dead") }, [
      championIcon(p.championId, p.name),
      el("div", { class: "who" }, [el("div", { class: "title" }, [p.name, ...tags]), stats]),
      el("div", { class: "right" }, [
        badge,
        el("span", { class: goldClass, title: goldTitle }, [
          `${goldText(diff)} oro`,
          vsLane ? el("small", { class: "versus", text: ` vs ${p.laneRival}` }) : null,
        ]),
      ]),
    ]);

    const key = `${side}:${p.championId || p.name}`;
    const before = memory.players.get(key);
    seen.players.set(key, { strength: p.strength, dead: Boolean(p.dead) });
    if (before && !seen.entering) {
      if (before.strength !== p.strength) {
        // Cambia de estado: la fila se ilumina con su color y la etiqueta salta.
        motion(() => {
          animate(row, [{ opacity: 0 }, { opacity: 1, offset: 0.2 }, { opacity: 0 }],
            { duration: reducedMotion.matches ? 600 : 1300, easing: "ease-out", pseudoElement: "::after" });
          move(badge, [{ transform: "scale(.6)" }, { transform: "none" }], { duration: 480, easing: EASE_POP });
        });
      }
      if (before.dead !== Boolean(p.dead)) {
        motion(() => animate(row, [{ opacity: before.dead ? 0.55 : 1 }, { opacity: p.dead ? 0.55 : 1 }],
          { duration: reducedMotion.matches ? 150 : 450, easing: "ease-out" }));
      }
    }
    return row;
  }));
}

render({ phaseLabel: "Conectando…", connected: false }, true);
if (DESKTOP) startDesktop();
else connect();
