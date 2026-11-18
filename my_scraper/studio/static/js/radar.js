/* Seine Studio: the hero's net background and the live crawl radar. */

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
  return () => { cancelAnimationFrame(raf); ro.disconnect(); };
}

// ---------------------------------------------------------------- crawl radar (live)
/**
 * The crawl as a radar: a radial tree of the site's URL paths. The site sits in the
 * middle, each top-level section gets a slice of the circle, and each ring is one
 * level deeper in the path. A sonar beam sweeps round and every page flares as it passes.
 */
export class Radar {
  constructor(canvas, tip, stats) {
    this.canvas = canvas; this.tip = tip; this.stats = stats; this.ctx = canvas.getContext("2d");
    this.nodes = new Map(); this.list = [];
    this.root = { part: "", children: new Map(), depth: 0, x: 0, y: 0, parent: null };
    this.view = { x: 0, y: 0, k: 1 }; this.hover = null; this.sweep = -Math.PI / 2; this.dirty = true;
    this.ro = new ResizeObserver(() => { this.size(); this.dirty = true; }); this.ro.observe(canvas); this.size();
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
      known.ping = now;
      return;
    }
    if (this.list.length > 900) return;  // the shape of a big crawl shows long before this
    let u; try { u = new URL(url); } catch (e) { return; }
    const parts = [u.host, ...u.pathname.split("/").filter(Boolean)];
    if (u.search) parts.push(u.search.length > 18 ? u.search.slice(0, 17) + "…" : u.search);
    let t = this.root;
    for (const part of parts) {
      if (!t.children.has(part)) t.children.set(part, { part, children: new Map(), depth: t.depth + 1, parent: t, x: t.x, y: t.y });
      t = t.children.get(part);
    }
    const node = { key, url, status, trie: t, born: now, ping: now, flare: 0 };
    t.page = node; this.nodes.set(key, node); this.list.push(node);
    this.dirty = true;
  }

  /** The middle of the radar: the one site, or the whole web when the crawl spans several. */
  center() { return this.root.children.size === 1 ? [...this.root.children.values()][0] : this.root; }
  layout() {
    const center = this.center(), leaves = new Map();
    const count = (t) => { let n = t.children.size ? 0 : 1; for (const c of t.children.values()) n += count(c); leaves.set(t, n); return n; };
    count(center);
    let deepest = 1;
    const depthOf = (t) => t.depth - center.depth;
    const walk = (t) => { deepest = Math.max(deepest, depthOf(t)); for (const c of t.children.values()) walk(c); };
    walk(center);
    this.rings = Math.min(5, deepest);
    this.cx = this.w / 2; this.cy = this.h / 2 + 16;  // below the title in the corner
    this.R = Math.max(40, Math.min(this.w / 2 - 64, (this.h - 80) / 2 - 24));  // room at the sides for section names
    this.gap = this.R / this.rings;
    this.sectors = [];
    const place = (t, a0, a1) => {
      const d = depthOf(t);
      t.angle = (a0 + a1) / 2; t.r = d === 0 ? 0 : this.gap * Math.min(d, this.rings);
      t.span = a1 - a0;
      const kids = [...t.children.values()].sort((a, b) => a.part.localeCompare(b.part));
      const total = kids.reduce((s, c) => s + leaves.get(c), 0) || 1;
      const pad = kids.length > 1 ? Math.min(.04, (a1 - a0) * .08 / kids.length) : 0;
      let a = a0;
      for (const c of kids) {
        const share = (a1 - a0) * leaves.get(c) / total;
        if (d === 0) this.sectors.push({ t: c, a0: a, a1: a + share });
        place(c, a + pad / 2, a + share - pad / 2);
        a += share;
      }
    };
    place(center, -Math.PI / 2, Math.PI * 1.5);
    this.centerNode = center;
    this.dirty = false;
  }
  pos(t) { return { x: this.cx + Math.cos(t.angle) * t.r, y: this.cy + Math.sin(t.angle) * t.r }; }

  frame(now) {
    const dt = Math.min(.05, (now - this.last) / 1000); this.last = now;
    if (this.dirty) this.layout();
    const { ctx } = this, v = this.view, t = tokens(), cx = this.cx, cy = this.cy, center = this.centerNode;
    const prev = this.sweep;
    this.sweep += dt * 1.25;
    if (this.sweep > Math.PI * 1.5) this.sweep -= Math.PI * 2;
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.save(); ctx.translate(v.x, v.y); ctx.scale(v.k, v.k);

    // rings, sector spokes and section names
    ctx.lineWidth = 1 / v.k; ctx.strokeStyle = t.line;
    ctx.setLineDash([2, 5]);
    for (let i = 1; i <= this.rings; i++) { ctx.globalAlpha = .55; ctx.beginPath(); ctx.arc(cx, cy, this.gap * i, 0, 6.283); ctx.stroke(); }
    ctx.setLineDash([]);
    ctx.font = `10.5px "Geist Mono", monospace`; ctx.textBaseline = "middle";
    for (const s of this.sectors) {
      if (this.sectors.length > 1) { ctx.globalAlpha = .35; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(s.a0) * (this.R + 6), cy + Math.sin(s.a0) * (this.R + 6)); ctx.stroke(); }
      if (s.a1 - s.a0 < .22) continue;
      const a = (s.a0 + s.a1) / 2, rx = cx + Math.cos(a) * (this.R + 16), ry = cy + Math.sin(a) * (this.R + 16);
      const align = Math.cos(a) > .3 ? "left" : Math.cos(a) < -.3 ? "right" : "center";
      ctx.globalAlpha = .75; ctx.fillStyle = t.ink3; ctx.textAlign = align;
      // fit the name into the room left before the card's edge
      const room = (align === "left" ? this.w - rx : align === "right" ? rx : Math.min(rx, this.w - rx) * 2) - 6;
      let label = "/" + s.t.part;
      while (label.length > 2 && ctx.measureText(label).width > room) label = label.slice(0, -2) + "…";
      if (label.length > 2) ctx.fillText(label, rx, ry);
    }
    ctx.globalAlpha = 1;

    // the sonar beam: a fading wedge behind a bright edge
    if (ctx.createConicGradient) {
      const g = ctx.createConicGradient(this.sweep - 1.1, cx, cy);
      g.addColorStop(0, "transparent"); g.addColorStop(.175, t.lime + "40"); g.addColorStop(.1751, "transparent"); g.addColorStop(1, "transparent");
      ctx.fillStyle = g; ctx.globalAlpha = .55;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, this.R + 4, this.sweep - 1.1, this.sweep); ctx.closePath(); ctx.fill();
    }
    ctx.globalAlpha = .8; ctx.strokeStyle = t.lime; ctx.lineWidth = 1.4 / v.k;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(this.sweep) * (this.R + 4), cy + Math.sin(this.sweep) * (this.R + 4)); ctx.stroke();
    ctx.globalAlpha = 1;

    // branches: each path bends out from its parent's ring, like a radial family tree
    const ease = Math.min(1, dt * 5), trie = [];
    const collect = (n) => { for (const c of n.children.values()) { trie.push(c); collect(c); } };
    collect(center);
    center.x += (cx - center.x) * ease; center.y += (cy - center.y) * ease;
    for (const n of trie) { const p = this.pos(n); n.x += (p.x - n.x) * ease; n.y += (p.y - n.y) * ease; }
    ctx.strokeStyle = t.line; ctx.lineWidth = 1 / v.k; ctx.globalAlpha = .8;
    ctx.beginPath();
    for (const n of trie) {
      const p = n.parent, bend = p === center ? { x: n.x, y: n.y } : { x: cx + Math.cos(n.angle) * p.r, y: cy + Math.sin(n.angle) * p.r };
      ctx.moveTo(p.x, p.y); ctx.quadraticCurveTo(bend.x, bend.y, n.x, n.y);
    }
    ctx.stroke(); ctx.globalAlpha = 1;

    // the beam lights up what it passes
    const passed = (a) => { let d = this.sweep - a, p = this.sweep - prev; d = ((d % 6.283) + 6.283) % 6.283; return d <= p + .001; };
    const colors = { ok: t.lime, refused: t.coral, error: t.amber, pending: t.ink3 };
    for (const n of trie) {
      const page = n.page;
      if (!page) { ctx.strokeStyle = t.ink3; ctx.globalAlpha = .7; ctx.beginPath(); ctx.arc(n.x, n.y, 2.2, 0, 6.283); ctx.stroke(); ctx.globalAlpha = 1; continue; }
      if (passed(n.angle)) page.flare = 1;
      page.flare = Math.max(0, page.flare - dt * .9);
      const color = colors[page.status] || t.ink3, hovered = page === this.hover;
      if (page.flare > 0) { ctx.globalAlpha = page.flare * .45; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, 3.5 + page.flare * 7, 0, 6.283); ctx.fill(); }
      const age = Math.max(0, (now - page.ping) / 1100);
      if (age < 1) { ctx.globalAlpha = 1 - age; ctx.strokeStyle = color; ctx.lineWidth = 1.6 / v.k; ctx.beginPath(); ctx.arc(n.x, n.y, 4 + age * 20, 0, 6.283); ctx.stroke(); }
      ctx.globalAlpha = .55 + page.flare * .45; ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(n.x, n.y, (hovered ? 5.5 : 3.4) * Math.min(1, Math.max(0, (now - page.born) / 300) + .3), 0, 6.283); ctx.fill();
    }
    ctx.globalAlpha = 1;
    // the site itself
    ctx.fillStyle = t.ink; ctx.beginPath(); ctx.arc(center.x, center.y, 6, 0, 6.283); ctx.fill();
    ctx.strokeStyle = t.lime; ctx.lineWidth = 1.5 / v.k; ctx.globalAlpha = .5 + Math.sin(now / 400) * .3;
    ctx.beginPath(); ctx.arc(center.x, center.y, 10, 0, 6.283); ctx.stroke(); ctx.globalAlpha = 1;
    if (center.part) { ctx.font = `600 11px "Geist Mono", monospace`; ctx.textAlign = "center"; ctx.fillStyle = t.ink; ctx.fillText(center.part, center.x, center.y + 22); }
    ctx.restore();

    if (this.stats) this.stats.textContent = `${nf.format(this.list.length)} pages · ${this.sectors.length} section${this.sectors.length === 1 ? "" : "s"} · ${this.rings} deep`;
    this.raf = requestAnimationFrame((n) => this.frame(n));
  }
  at(px, py) {
    const v = this.view, x = (px - v.x) / v.k, y = (py - v.y) / v.k;
    let best = null, bd = 120 / (v.k * v.k);
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
    c.addEventListener("wheel", (e) => {
      e.preventDefault(); const [x, y] = pos(e), k = Math.max(.5, Math.min(4, this.view.k * (e.deltaY < 0 ? 1.12 : .89)));
      this.view.x = x - (x - this.view.x) * (k / this.view.k); this.view.y = y - (y - this.view.y) * (k / this.view.k); this.view.k = k;
    }, { passive: false });
  }
  destroy() { cancelAnimationFrame(this.raf); this.ro.disconnect(); }
}
