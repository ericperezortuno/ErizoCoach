"use strict";

// La ventana solo pinta el estado que envía LoL Coach por el websocket.
// Todo el texto se inserta con textContent (nunca como HTML).

const token = new URLSearchParams(location.search).get("t") || "";
const app = document.getElementById("app");
let retries = 0;

function connect() {
  const socket = new WebSocket(`ws://${location.host}/ws?t=${encodeURIComponent(token)}`);
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
  document.getElementById("connection-text").textContent = on
    ? "Conectado a LoL Coach"
    : "Sin conexión con LoL Coach (¿está abierto?)";
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

function itemIcon(item, { big = false, owned = false, next = false, count = 1 } = {}) {
  if (!item) return null;
  const classes = ["icon", big && "big", owned && "owned", next && "next"].filter(Boolean).join(" ");
  const label = item.price ? `${item.name} (${item.price} de oro)` : item.name;
  return el("span", { class: classes, title: label }, [
    el("img", { src: `/icon/item/${item.id}.png`, alt: item.name, loading: "lazy" }),
    count > 1 ? el("span", { class: "count", text: `x${count}` }) : null,
  ]);
}

function championIcon(id, name) {
  if (!id) return el("span", { class: "icon champ" });
  return el("span", { class: "icon champ", title: name }, [
    el("img", { src: `/icon/champion/${id}.png`, alt: name, loading: "lazy" }),
  ]);
}

function card(title, children) {
  return el("section", { class: "card" }, [el("h2", { text: title }), ...children]);
}

function goldText(diff) {
  const abs = Math.abs(diff);
  const text = abs >= 1000 ? `${(abs / 1000).toFixed(1).replace(".", ",")}k` : `${abs}`;
  return `${diff >= 0 ? "+" : "−"}${text}`;
}

function bulletList(items) {
  return el("ul", { class: "list" }, items.map((text) => el("li", { text })));
}

// --- Vistas ------------------------------------------------------------------------

function render(state) {
  document.getElementById("phase").textContent = state.phaseLabel || "";
  document.getElementById("clock").textContent = state.game ? state.game.time : "";
  document.getElementById("version").textContent = state.version ? `v${state.version}` : "";

  let sections;
  if (state.game) sections = renderGame(state);
  else if (state.champion || state.loadout || state.build) sections = renderChampSelect(state);
  else sections = [renderIdle(state)];
  app.replaceChildren(...sections.filter(Boolean));
}

function renderIdle(state) {
  const title = state.connected ? "Esperando a la selección de campeones" : "Esperando al cliente de LoL";
  const hint = state.connected
    ? "Cuando se haga el último pick verás aquí tus runas y tu build; durante la partida, qué comprar y quién va fuerte."
    : "Abre el cliente de League of Legends: LoL Coach se conectará solo.";
  return el("div", { class: "empty" }, [el("div", { class: "big", text: title }), el("div", { text: hint })]);
}

function renderChampSelect(state) {
  return [
    state.champion ? renderHero(state) : null,
    state.loadout ? renderLoadout(state.loadout) : null,
    state.build ? renderBuild(state.build) : null,
  ];
}

function renderHero(state) {
  const champion = state.champion;
  const subtitle = [champion.position, state.build && state.build.archetype].filter(Boolean).join(" · ");
  return el("section", { class: "card hero" }, [
    championIcon(champion.id, champion.name),
    el("div", {}, [el("div", { class: "name", text: champion.name }), el("div", { class: "muted", text: subtitle })]),
  ]);
}

function renderLoadout(loadout) {
  const rows = [];
  if (loadout.keystone) {
    rows.push(el("div", { class: "keystone", text: `${loadout.keystone} · ${loadout.primaryStyle}` }));
    rows.push(el("div", { class: "row" }, [el("b", { text: loadout.primaryStyle }), loadout.primary.join(", ")]));
    rows.push(el("div", { class: "row" }, [el("b", { text: loadout.secondaryStyle }), loadout.secondary.join(", ")]));
    rows.push(el("div", { class: "row" }, [el("b", { text: "Fragmentos" }), loadout.shards.join(", ")]));
  }
  if (loadout.spells.length) {
    rows.push(el("div", { class: "row" }, [el("b", { text: "Hechizos" }),
      el("span", { class: "chips" }, loadout.spells.map((s) => el("span", { class: "chip", text: s.name })))]));
  }
  if (loadout.laneOpponent) rows.push(el("div", { class: "muted small", text: `Rival de línea probable: ${loadout.laneOpponent}` }));
  const extra = [...loadout.adjustments, ...loadout.notes];
  return card("Runas y hechizos", [el("div", { class: "runes" }, rows), extra.length ? bulletList(extra) : null]);
}

function renderBuild(build) {
  const children = [
    el("div", { class: "section-label", text: "Inicio" }),
    el("div", { class: "icons" }, build.starting.map((i) => itemIcon(i, { count: i.count }))),
    el("div", { class: "section-label", text: "Núcleo, en orden" }),
    el("div", { class: "icons" }, build.core.flatMap((item, index) => [
      index ? el("span", { class: "arrow", text: "›" }) : null, itemIcon(item),
    ])),
    el("div", { class: "section-label", text: "Build completa" }),
    el("div", { class: "icons" }, build.full.map((i) => itemIcon(i))),
  ];
  if (build.situational.length) {
    children.push(el("div", { class: "section-label", text: "Contra este equipo" }));
    children.push(el("div", { class: "situational" }, build.situational.map((s) =>
      el("div", { class: "entry" }, [itemIcon(s), el("span", { class: "small", text: s.reason })]))));
  }
  const notes = [...build.notes];
  if (notes.length) children.push(bulletList(notes));
  return card(`Build · ${build.archetype}`, children);
}

function renderGame(state) {
  const game = state.game;
  return [
    renderPurchase(game),
    game.buildProgress.length ? renderProgress(game) : null,
    game.enemies.length ? card("Rivales", [renderPlayers(game.enemies)]) : null,
    game.allies.length || game.me
      ? card("Tu equipo", [renderPlayers([game.me, ...game.allies].filter(Boolean))])
      : null,
  ];
}

function renderPurchase(game) {
  const next = game.next;
  if (!next) {
    return card("Compra ahora", [el("div", { class: "muted", text: game.message || "Sin recomendación de compra en este modo." })]);
  }
  const status = next.canBuy
    ? el("div", { class: "ok", text: "Te llega el oro: ¡cómpralo!" })
    : el("div", { class: "warn", text: `Te faltan ${next.remaining} de oro` });
  const components = next.components.length
    ? el("div", { class: "components" }, [el("span", { class: "small muted", text: "Ya puedes comprar:" }),
      ...next.components.map((c) => itemIcon(c))])
    : null;
  return card(`Compra ahora · ${game.gold} de oro`, [
    el("div", { class: "buy" }, [
      itemIcon(next, { big: true }),
      el("div", {}, [el("div", { class: "name", text: next.name }), el("div", { class: "reason", text: next.reason }), status, components]),
    ]),
  ]);
}

function renderProgress(game) {
  const nextId = game.next ? game.next.id : null;
  const icons = game.buildProgress.map((item) => itemIcon(item, { owned: item.owned, next: item.id === nextId }));
  return card("Tu build", [el("div", { class: "icons" }, icons)]);
}

function renderPlayers(players) {
  return el("div", { class: "players" }, players.map((p) => {
    const tags = [
      p.lane ? el("span", { class: "tag lane", text: "Tu rival" }) : null,
      p.damage === "AP" ? el("span", { class: "tag dmg-ap", text: "AP" }) : null,
      p.damage === "AD" ? el("span", { class: "tag dmg-ad", text: "AD" }) : null,
    ];
    const stats = `${p.kda} · ${p.cs} CS · Nv ${p.level}` + (p.dead && p.respawn ? ` · reaparece en ${p.respawn} s` : "");
    const goldClass = p.goldDiff > 400 ? "gold up" : p.goldDiff < -400 ? "gold down" : "gold";
    return el("div", { class: `player ${p.strength}${p.dead ? " dead" : ""}` }, [
      championIcon(p.championId, p.name),
      el("div", {}, [el("div", { class: "title" }, [p.name, ...tags]), el("div", { class: "stats", text: stats })]),
      el("div", { class: "right" }, [
        el("span", { class: `badge ${p.strength}`, text: p.strengthLabel }),
        el("span", { class: goldClass, title: "Oro en objetos frente a la media de la partida", text: `${goldText(p.goldDiff)} oro` }),
      ]),
    ]);
  }));
}

render({ phaseLabel: "Conectando…", connected: false });
connect();
