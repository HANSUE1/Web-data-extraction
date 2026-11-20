/* Seine Studio: the hero's net background and the live silk map of a crawl. */

import { reduced, nf, el, tokens } from "./ui.js";

// ---------------------------------------------------------------- net background (hero)
export function heroNet(canvas) {
  const ctx = canvas.getContext("2d");
  let w, h, pts = [], cols = 0, raf, mouse = { x: -999, y: -999 }, t0 = performance.now();
  function size() {
    const r = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    w = r.width; h = r.height; canvas.width = w * dpr; canvas.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    pts = [];
    const gap = 46;
    cols = 0;
    for (let x = -gap; x < w + gap; x += gap) cols++;
    for (let y = -gap; y < h + gap; y += gap) for (let x = -gap; x < w + gap; x += gap) {
      const ox = x + (Math.random() - .5) * 18, oy = y + (Math.random() - .5) * 18;
      pts.push({ ox, oy, x: ox, y: oy, vx: 0, vy: 0, ph: Math.random() * 6.28 });
    }
  }
  function frame(now) {
    const t = (now - t0) / 1000;
    ctx.clearRect(0, 0, w, h);
    for (const p of pts) {
      const tx = p.ox + Math.sin(t * .6 + p.ph + p.oy * .01) * 5, ty = p.oy + Math.cos(t * .5 + p.ph + p.ox * .008) * 7;
      const dx = p.x - mouse.x, dy = p.y - mouse.y, d2 = dx * dx + dy * dy;
      if (d2 < 22000) { const f = (1 - d2 / 22000) * 2.2, d = Math.sqrt(d2) || 1; p.vx += dx / d * f; p.vy += dy / d * f; }
      p.vx += (tx - p.x) * .04; p.vy += (ty - p.y) * .04; p.vx *= .82; p.vy *= .82; p.x += p.vx; p.y += p.vy;
    }
    ctx.lineWidth = 1;
    const { line: lineColor, lime } = tokens();
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      for (const j of [i + 1, i + cols]) {
        const q = pts[j]; if (!q || (j === i + 1 && Math.abs(q.oy - p.oy) > 30)) continue;
        const mx = (p.x + q.x) / 2 - mouse.x, my = (p.y + q.y) / 2 - mouse.y, near = Math.max(0, 1 - Math.sqrt(mx * mx + my * my) / 190);
        ctx.strokeStyle = near > 0 ? lime : lineColor;
        ctx.globalAlpha = near > 0 ? .25 + near * .6 : .35 * Math.min(1, (p.x / w) * 1.6);
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    if (!reduced) raf = requestAnimationFrame(frame);
  }
  const onMove = (e) => { const r = canvas.getBoundingClientRect(); mouse = { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const onLeave = () => { mouse = { x: -999, y: -999 }; };
  const host = canvas.parentElement;
  host.addEventListener("pointermove", onMove); host.addEventListener("pointerleave", onLeave);
  const ro = new ResizeObserver(() => size()); ro.observe(canvas);
  size(); raf = requestAnimationFrame(frame);
  // scrolled out of view: stop drawing, it would burn a frame loop nobody sees
  const io = new IntersectionObserver(([e]) => {
    if (!e.isIntersecting) { cancelAnimationFrame(raf); raf = 0; } else if (!raf) raf = requestAnimationFrame(frame);
  });
  io.observe(canvas);
  return () => { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); };
}

// ---------------------------------------------------------------- silk map (live)
/**
 * The crawl as a living web. Every URL path hangs on a silk strand from its parent path, so
 * sections pull into their own clusters by themselves. Strands are springs: when a page is
 * fetched, a pulse runs from the hub out along the strands to it and kicks the web, which
 * trembles and settles, the way a spider feels where something has landed.
 */
export class SilkMap {
  constructor(canvas, tip, stats) {
    this.canvas = canvas; this.tip = tip; this.stats = stats; this.ctx = canvas.getContext("2d");
    this.nodes = new Map(); this.list = []; this.knots = [];
    this.root = { part: "", children: new Map(), depth: 0, parent: null, x: 0, y: 0, vx: 0, vy: 0 };
    this.view = { x: 0, y: 0, k: 1 }; this.fit = 1; this.hover = null; this.heat = 1; this.pulses = [];
    this.ro = new ResizeObserver(() => this.size()); this.ro.observe(canvas); this.size();
    this.bind();
    this.last = performance.now();
    this.raf = requestAnimationFrame((t) => this.frame(t));
  }
  size() { const r = this.canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1; this.w = r.width; this.h = r.height; this.canvas.width = r.width * dpr; this.canvas.height = r.height * dpr; this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
  key(url) { try { const u = new URL(url); return u.host + u.pathname.replace(/\/+$/, "") + (u.search || ""); } catch (e) { return url; } }

  add(url, status) {
    const key = this.key(url), now = performance.now();
    const known = this.nodes.get(key);
    if (known) {
      if (!(status === "pending" && known.status !== "pending")) known.status = status;  // never forget a page was fetched
      if (status !== "pending") this.pulse(known.trie);
      known.ping = now;
      return;
    }
    if (this.list.length > 900) return;  // the shape of a big crawl shows long before this
    let u; try { u = new URL(url); } catch (e) { return; }
    const parts = [u.host, ...u.pathname.split("/").filter(Boolean)];
    if (u.search) parts.push(u.search.length > 18 ? u.search.slice(0, 17) + "…" : u.search);
    let t = this.root;
    for (const part of parts) {
      if (!t.children.has(part)) {  // a new strand spun out of its parent, in a random direction
        const a = Math.random() * 6.283, kid = { part, children: new Map(), depth: t.depth + 1, parent: t,
          x: t.x + Math.cos(a) * 8, y: t.y + Math.sin(a) * 8, vx: 0, vy: 0, kick: 0 };
        t.children.set(part, kid); this.knots.push(kid);
      }
      t = t.children.get(part);
    }
    const node = { key, url, status, trie: t, born: now, ping: now };
    t.page = node; this.nodes.set(key, node); this.list.push(node);
    this.heat = 1;
    if (status !== "pending") this.pulse(t);
  }
  pulse(t) {
    const path = [];
    for (let n = t; n && n !== this.hub; n = n.parent) path.unshift(n);
    if (t === this.hub) t.ring = performance.now();
    if (!this.hub || !path.length || this.pulses.length > 24) return;
    path.unshift(this.hub);
    this.pulses.push({ path, t: 0 });
    for (const n of path) if (n !== this.hub) { const a = Math.random() * 6.283; n.vx += Math.cos(a) * 2.2; n.vy += Math.sin(a) * 2.2; }  // the strands twang
    this.heat = Math.max(this.heat, .35);
  }

  /** The hub: the one site, or the whole web when the crawl spans several. */
  get hub() { return this.root.children.size === 1 ? [...this.root.children.values()][0] : this.root; }
  step(s) {
    const hub = this.hub, live = this.knots.filter((n) => n !== hub && this.under(n, hub)), heat = this.heat;
    hub.x = hub.y = hub.vx = hub.vy = 0;
    for (const n of live) { n.fx = -n.x * .0025; n.fy = -n.y * .0025; }  // a faint pull home keeps loose clusters in frame
    for (const n of live) {  // strands: springs to the parent, shorter for leaf pages than for sections
      const p = n.parent, dx = n.x - p.x, dy = n.y - p.y, d = Math.hypot(dx, dy) || .01;
      // a big cluster needs a longer strand to its parent and a wider rim, or it piles onto its neighbours
      const rest = n.children.size ? Math.max(34, 62 - (n.depth - hub.depth) * 8) + Math.sqrt(n.children.size) * 10
        : p === hub ? 46 : 18 + Math.sqrt(p.children.size) * 4, f = (d - rest) * .09;
      n.fx -= dx / d * f; n.fy -= dy / d * f;
      if (p !== hub) { const share = .5 / p.children.size; p.fx += dx / d * f * share; p.fy += dy / d * f * share; }  // split, or a big knot's springs add up and it explodes
    }
    // everything pushes its neighbours away; a coarse grid keeps that cheap for hundreds of knots
    const cell = 46, grid = new Map(), all = [hub, ...live];
    for (const n of all) { const k = Math.floor(n.x / cell) + "," + Math.floor(n.y / cell); (grid.get(k) || grid.set(k, []).get(k)).push(n); }
    for (const n of live) {
      const gx = Math.floor(n.x / cell), gy = Math.floor(n.y / cell);
      for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (const m of grid.get(gx + i + "," + (gy + j)) || []) {
        if (m === n) continue;
        let dx = n.x - m.x, dy = n.y - m.y, d2 = dx * dx + dy * dy;
        if (d2 > cell * cell * 2) continue;
        if (d2 < .01) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = .25; }
        const f = Math.min(4, (m === hub ? 520 : 260) / d2), d = Math.sqrt(d2);
        n.fx += dx / d * f; n.fy += dy / d * f;
      }
    }
    for (const n of live) {
      n.vx = (n.vx + n.fx * s * (.25 + heat)) * Math.pow(.8, s); n.vy = (n.vy + n.fy * s * (.25 + heat)) * Math.pow(.8, s);
      const v = Math.hypot(n.vx, n.vy); if (v > 12) { n.vx *= 12 / v; n.vy *= 12 / v; }  // shortcut: speed cap keeps a bad frame from flinging knots off-screen
      n.x += n.vx * s; n.y += n.vy * s;
    }
    return live;
  }
  under(n, hub) { for (let p = n; p; p = p.parent) if (p === hub) return true; return false; }
  origin() { return { x: this.w / 2 + this.view.x, y: this.h / 2 + 18 + this.view.y, k: this.fit * this.view.k }; }

  frame(now) {
    const dt = Math.max(0, Math.min(.05, (now - this.last) / 1000)); this.last = now;  // the first frame can stamp earlier than the constructor
    const { ctx } = this, t = tokens(), hub = this.hub, live = this.step(dt * 60);
    this.heat = Math.max(.04, this.heat - dt * .35);
    // zoom to fit the web, gently, with room for the title and legend
    let ext = 60;
    for (const n of live) ext = Math.max(ext, Math.abs(n.x) / Math.max(40, this.w / 2 - 110) * (this.h / 2 - 60), Math.abs(n.y));  // 110px each side keeps section names inside the card
    const want = Math.max(.35, Math.min(1.5, (this.h / 2 - 60) / ext));
    this.fit += (want - this.fit) * Math.min(1, dt * 2);
    const o = this.origin();
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.save(); ctx.translate(o.x, o.y); ctx.scale(o.k, o.k);
    const px = 1 / o.k;  // one screen pixel, in web units

    // capture threads: around every knot with a few strands, neighbouring strands are tied together
    // by a thread that bows in toward the knot, so each cluster reads as its own small orb web
    ctx.globalAlpha = .2; ctx.strokeStyle = t.ink3; ctx.lineWidth = .7 * px; ctx.beginPath();
    for (const k of [hub, ...live]) {
      if (k.children.size < 3) continue;
      const kids = [...k.children.values()].map((c) => [Math.atan2(c.y - k.y, c.x - k.x), c]).sort((a, b) => a[0] - b[0]);
      for (let i = 0; i < kids.length; i++) {
        const [a0, a] = kids[i], [a1, b] = kids[(i + 1) % kids.length], gap = ((a1 - a0) + 6.283) % 6.283;
        if (gap > 1.4 || gap === 0) continue;  // too far round: real webs leave the gap open
        for (const f of [.62, .92]) {  // two turns of the spiral
          const ax = k.x + (a.x - k.x) * f, ay = k.y + (a.y - k.y) * f, bx = k.x + (b.x - k.x) * f, by = k.y + (b.y - k.y) * f;
          const mx = (ax + bx) / 2, my = (ay + by) / 2;
          ctx.moveTo(ax, ay); ctx.quadraticCurveTo(mx + (k.x - mx) * .18, my + (k.y - my) * .18, bx, by);
        }
      }
    }
    ctx.stroke();
    // strands: silk sags a little under its own weight; sections are a touch heavier than leaves
    const lit = new Set();
    if (this.hover) for (let n = this.hover.trie; n && n !== hub; n = n.parent) lit.add(n);
    ctx.lineCap = "round"; ctx.strokeStyle = t.ink3;
    for (const [alpha, width, test] of [[.35, .8, (n) => !n.children.size], [.6, 1.2, (n) => n.children.size]]) {
      ctx.globalAlpha = alpha; ctx.lineWidth = width * px; ctx.beginPath();
      for (const n of live) {
        if (!test(n)) continue;
        const p = n.parent, len = Math.hypot(n.x - p.x, n.y - p.y);
        ctx.moveTo(p.x, p.y); ctx.quadraticCurveTo((p.x + n.x) / 2, (p.y + n.y) / 2 + len * .07, n.x, n.y);
      }
      ctx.stroke();
    }
    if (lit.size) {  // the hovered page's line back to the hub
      ctx.globalAlpha = .9; ctx.strokeStyle = t.lime; ctx.lineWidth = 1.6 * px; ctx.beginPath();
      for (const n of lit) { const p = n.parent, len = Math.hypot(n.x - p.x, n.y - p.y); ctx.moveTo(p.x, p.y); ctx.quadraticCurveTo((p.x + n.x) / 2, (p.y + n.y) / 2 + len * .07, n.x, n.y); }
      ctx.stroke();
    }
    // pulses: a bead of light running out along the strands to the page just fetched
    const colors = { ok: t.lime, refused: t.coral, error: t.amber, pending: t.ink3 };
    for (const pl of this.pulses) {
      pl.t += dt * 5.5;  // segments per second
      const i = Math.min(pl.path.length - 2, Math.floor(pl.t)), f = Math.min(1, pl.t - i), a = pl.path[i], b = pl.path[i + 1];
      const len = Math.hypot(b.x - a.x, b.y - a.y), cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2 + len * .07;
      const x = (1 - f) * (1 - f) * a.x + 2 * (1 - f) * f * cx + f * f * b.x, y = (1 - f) * (1 - f) * a.y + 2 * (1 - f) * f * cy + f * f * b.y;
      ctx.globalAlpha = .85; ctx.strokeStyle = t.lime; ctx.lineWidth = 1.4 * px; ctx.beginPath(); ctx.moveTo(a.x, a.y);
      ctx.quadraticCurveTo((1 - f) * a.x + f * cx, (1 - f) * a.y + f * cy, x, y); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = t.lime; ctx.beginPath(); ctx.arc(x, y, 2.6 * px, 0, 6.283); ctx.fill();
      if (pl.t >= pl.path.length - 1) { pl.done = true; b.ring = now; }
    }
    this.pulses = this.pulses.filter((pl) => !pl.done);

    // knots: path segments with no page of their own are just where strands meet
    ctx.fillStyle = t.ink3;
    for (const n of live) if (!n.page) { ctx.globalAlpha = .7; ctx.beginPath(); ctx.arc(n.x, n.y, 1.8 * px, 0, 6.283); ctx.fill(); }
    // pages: dewdrops on the web, coloured by what happened, with a glint and a ripple when the pulse arrives
    for (const n of live) {
      const page = n.page; if (!page) continue;
      const color = colors[page.status] || t.ink3, grow = Math.min(1, (now - page.born) / 350);
      const r = (page === this.hover ? 5.5 : n.children.size ? 4.4 : 3.5) * (.3 + grow * .7) * px;
      const ring = n.ring ? (now - n.ring) / 900 : 1;
      if (ring < 1) { ctx.globalAlpha = 1 - ring; ctx.strokeStyle = color; ctx.lineWidth = 1.4 * px; ctx.beginPath(); ctx.arc(n.x, n.y, r + ring * 18 * px, 0, 6.283); ctx.stroke(); }
      ctx.globalAlpha = page.status === "pending" ? .55 : .95; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.283); ctx.fill();
      ctx.globalAlpha = .7; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(n.x - r * .35, n.y - r * .35, r * .3, 0, 6.283); ctx.fill();
    }
    // section names beside their knots, pushed outward from the hub
    const sections = [...hub.children.values()];
    ctx.globalAlpha = .8; ctx.font = `${10.5 * px}px "Geist Mono", monospace`; ctx.textBaseline = "middle"; ctx.fillStyle = t.ink3;
    if (sections.length <= 30) for (const n of sections) {
      if (!n.children.size && sections.length > 6) continue;  // lone pages are named on hover; clusters always
      let reach = 0; for (const c of n.children.values()) reach = Math.max(reach, Math.hypot(c.x - n.x, c.y - n.y));
      const d = Math.hypot(n.x, n.y) || 1, right = n.x >= 0, out = reach + 10 * px;  // past the cluster's rim, away from the hub
      ctx.textAlign = Math.abs(n.x / d) < .35 ? "center" : right ? "left" : "right";
      const label = "/" + (n.part.length > 22 ? n.part.slice(0, 21) + "…" : n.part);
      ctx.fillText(label, n.x + n.x / d * out, n.y + n.y / d * out);
    }
    // the hub, where the spider sits
    ctx.globalAlpha = 1; ctx.fillStyle = t.ink; ctx.beginPath(); ctx.arc(0, 0, 6 * px, 0, 6.283); ctx.fill();
    ctx.strokeStyle = t.lime; ctx.lineWidth = 1.5 * px; ctx.globalAlpha = .5 + Math.sin(now / 400) * .3;
    ctx.beginPath(); ctx.arc(0, 0, 10 * px, 0, 6.283); ctx.stroke(); ctx.globalAlpha = 1;
    if (hub.part) { ctx.font = `600 ${11 * px}px "Geist Mono", monospace`; ctx.textAlign = "center"; ctx.fillStyle = t.ink; ctx.fillText(hub.part, 0, 22 * px); }
    ctx.restore();

    if (this.stats) {
      let deep = 0; for (const n of live) deep = Math.max(deep, n.depth - hub.depth);
      this.stats.textContent = `${nf.format(this.list.length)} pages · ${sections.length} section${sections.length === 1 ? "" : "s"} · ${deep} deep`;
    }
    this.raf = requestAnimationFrame((n) => this.frame(n));
  }
  at(sx, sy) {
    const o = this.origin(), x = (sx - o.x) / o.k, y = (sy - o.y) / o.k;
    let best = null, bd = 120 / (o.k * o.k);
    for (const p of this.list) { const d = (p.trie.x - x) ** 2 + (p.trie.y - y) ** 2; if (d < bd) { bd = d; best = p; } }
    return best;
  }
  bind() {
    const c = this.canvas; let pan = null;
    const pos = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener("pointerdown", (e) => { const [x, y] = pos(e); c.setPointerCapture(e.pointerId); pan = { x, y, vx: this.view.x, vy: this.view.y }; });
    c.addEventListener("pointermove", (e) => {
      const [x, y] = pos(e);
      if (pan) { this.view.x = pan.vx + x - pan.x; this.view.y = pan.vy + y - pan.y; return; }
      this.hover = this.at(x, y);
      if (this.hover) { this.tip.replaceChildren(el("b", { text: this.hover.status + " " }), this.hover.url); this.tip.style.left = x + "px"; this.tip.style.top = y + "px"; this.tip.classList.add("on"); }
      else this.tip.classList.remove("on");
    });
    c.addEventListener("pointerup", () => { pan = null; });
    c.addEventListener("pointerleave", () => { this.hover = null; this.tip.classList.remove("on"); });
    c.addEventListener("dblclick", (e) => { const [x, y] = pos(e); const hit = this.at(x, y); if (hit) window.open(hit.url, "_blank", "noopener"); else this.view = { x: 0, y: 0, k: 1 }; });
    c.addEventListener("wheel", (e) => {  // Ctrl + wheel zooms about the cursor; a plain wheel scrolls the page as usual
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault(); const [x, y] = pos(e), k = Math.max(.5, Math.min(4, this.view.k * (e.deltaY < 0 ? 1.12 : .89)));
      const ox = this.w / 2, oy = this.h / 2 + 18;
      this.view.x = x - ox - (x - ox - this.view.x) * (k / this.view.k); this.view.y = y - oy - (y - oy - this.view.y) * (k / this.view.k); this.view.k = k;
    }, { passive: false });
  }
  destroy() { cancelAnimationFrame(this.raf); this.ro.disconnect(); }
}
