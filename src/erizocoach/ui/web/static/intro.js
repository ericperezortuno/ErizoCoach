// Erizo Coach · intro de ~5 s al abrir League of Legends (ui/intro.py).
// La app la arranca con window.erizoIntro.play(): el CSS lleva el ritmo y aquí van las
// letras del título y las chispas (un canvas; sin chispas si Windows tiene las animaciones
// desactivadas).
"use strict";

(() => {
  const IMPACT_MS = 1250; // el medallón cae (igual que --impact en intro.css)
  const OUT_MS = 4150; // empieza a apagarse (--out)
  const END_MS = 5200; // la ventana ya se ha cerrado
  const stage = document.getElementById("stage");
  const canvas = document.getElementById("sparks");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  for (const line of document.querySelectorAll(".line")) {
    const first = Number(line.dataset.first || 0);
    [...line.dataset.text].forEach((ch, i) => {
      const letter = document.createElement("span");
      letter.className = "ch";
      letter.textContent = ch === " " ? " " : ch;
      letter.style.setProperty("--i", String(first + i)); // CSSOM: la CSP no deja style=""
      line.append(letter);
    });
  }

  let played = false;
  function play() {
    if (played) return;
    played = true;
    stage.classList.add("play");
    if (!reduce) sparks();
  }

  // --- Chispas -------------------------------------------------------------------------
  // Brasas que suben todo el rato y una explosión dorada en el impacto. Cada chispa es una
  // imagen de brillo ya pintada (rápido) sumada en modo «lighter» (se ve como luz).

  function glowSprite(color) {
    const size = 64;
    const sprite = document.createElement("canvas");
    sprite.width = sprite.height = size;
    const g = sprite.getContext("2d");
    const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.18, color);
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    return sprite;
  }

  function sparks() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = (canvas.width = Math.round(innerWidth * dpr));
    const height = (canvas.height = Math.round(innerHeight * dpr));
    const unit = Math.min(width, height) / 1080; // escala: 1 en un monitor 1080p
    const sprites = [glowSprite("rgba(242,223,174,.9)"), glowSprite("rgba(255,173,66,.85)"), glowSprite("rgba(156,206,255,.85)")];
    const medallion = document.querySelector(".medallion").getBoundingClientRect();
    const cx = (medallion.left + medallion.width / 2) * dpr;
    const cy = (medallion.top + medallion.height / 2) * dpr;
    const rand = (a, b) => a + Math.random() * (b - a);

    const embers = [];
    function ember(anywhere) {
      return {
        x: rand(0, width),
        y: anywhere ? rand(height * 0.2, height) : height + rand(0, 40 * unit),
        vx: rand(-0.25, 0.25) * unit,
        vy: -rand(0.6, 2.2) * unit,
        size: rand(5, 16) * unit,
        phase: rand(0, Math.PI * 2),
        sprite: sprites[Math.random() < 0.8 ? 0 : 1],
      };
    }
    for (let i = 0; i < 90; i++) embers.push(ember(true));

    const burst = [];
    function explode() {
      for (let i = 0; i < 260; i++) {
        const angle = rand(0, Math.PI * 2);
        const speed = rand(3, 24) * unit;
        burst.push({
          x: cx, y: cy,
          vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
          life: rand(0.7, 1.7), age: 0,
          size: rand(6, 20) * unit,
          sprite: sprites[Math.random() < 0.72 ? 0 : Math.random() < 0.6 ? 1 : 2],
        });
      }
    }

    const start = performance.now();
    let last = start;
    let exploded = false;
    function frame(now) {
      const t = now - start;
      const dt = Math.min((now - last) / 1000, 0.05);
      const k = dt * 60; // pasos a 60 fps
      last = now;
      if (!exploded && t >= IMPACT_MS) {
        exploded = true;
        explode();
      }
      // Las brasas entran poco a poco y se apagan con el final.
      const fade = Math.min(1, t / 700) * (t > OUT_MS ? Math.max(0, 1 - (t - OUT_MS) / 700) : 1);
      ctx.clearRect(0, 0, width, height);
      ctx.globalCompositeOperation = "lighter";

      for (const e of embers) {
        e.phase += 0.05 * k;
        e.x += (e.vx + Math.sin(e.phase) * 0.35 * unit) * k;
        e.y += e.vy * k;
        if (e.y < -20 * unit) Object.assign(e, ember(false));
        const flicker = 0.55 + 0.45 * Math.sin(e.phase * 2.3);
        ctx.globalAlpha = fade * flicker * Math.min(1, e.y / (height * 0.35));
        ctx.drawImage(e.sprite, e.x - e.size / 2, e.y - e.size / 2, e.size, e.size);
      }

      for (let i = burst.length - 1; i >= 0; i--) {
        const p = burst[i];
        p.age += dt;
        if (p.age >= p.life) {
          burst.splice(i, 1);
          continue;
        }
        const drag = Math.pow(0.955, k);
        p.vx *= drag;
        p.vy = p.vy * drag + 0.06 * unit * k; // un poco de gravedad
        const px = p.x;
        const py = p.y;
        p.x += p.vx * k;
        p.y += p.vy * k;
        const alive = 1 - p.age / p.life;
        // Estela: la chispa se estira en la dirección en la que va (sensación de velocidad).
        ctx.globalAlpha = alive * 0.55;
        ctx.strokeStyle = "rgba(242,223,174,1)";
        ctx.lineWidth = Math.max(1, p.size * 0.18 * alive);
        ctx.beginPath();
        ctx.moveTo(px - p.vx * 1.5 * k, py - p.vy * 1.5 * k);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
        ctx.globalAlpha = alive;
        const s = p.size * (0.6 + alive * 0.6);
        ctx.drawImage(p.sprite, p.x - s / 2, p.y - s / 2, s, s);
      }

      ctx.globalAlpha = 1;
      if (t < END_MS) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  window.erizoIntro = { play };
})();
