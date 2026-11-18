/* Seine Studio: run extractions, watch them live, browse what they caught. No build step. */
(() => {
  "use strict";

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const nf = new Intl.NumberFormat();

  /** Build an element. Text always goes in as text, never as HTML. */
  function el(tag, props = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "style") node.style.cssText = value;
      else if (key === "value") node.value = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key in node && typeof value !== "string") node[key] = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid);
    return node;
  }

  const ICONS = {
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    trash: '<path d="M4 7h16m-10 4v6m4-6v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    download: '<path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
    wand: '<path d="m15 4 1 2 2 1-2 1-1 2-1-2-2-1 2-1zM4 20 15 9m3 6 .7 1.3L20 17l-1.3.7L18 19l-.7-1.3L16 17l1.3-.7z"/>',
    sparkles: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    code: '<path d="m8 8-4 4 4 4m8-8 4 4-4 4m-5 3 2-14"/>',
    page: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6m-6 4h6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    grid: '<path d="M4 4h7v7H4zm9 0h7v7h-7zM4 13h7v7H4zm9 0h7v7h-7z"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    columns: '<path d="M4 4h16v16H4zM10 4v16m4-16v16"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    radar: '<circle cx="12" cy="12" r="3"/><path d="M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>',
    home: '<path d="M4 13h6V4H4zm10 7h6V11h-6zM4 20h6v-4H4zm10-11h6V4h-6z"/>',
    books: '<path d="M4 5h5v15H4zm6 0h4v15h-4zm5.5.6 4.3-1 3.3 14.6-4.3 1z"/>',
    net: '<path d="M5 9q7 6 14 0M5 15q7 6 14 0"/>',
  };
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.innerHTML = ICONS[name] || "";  // constant markup from the table above
    return svg;
  }

  async function api(path, { method = "GET", body, query } = {}) {
    const url = new URL(path, location.origin);
    for (const [k, v] of Object.entries(query || {})) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    const headers = { "X-Studio": "1" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error((data && (data.message || data.error)) || `HTTP ${res.status}`);
    return data;
  }

  function toast(message, bad = false, onClick) {
    const node = el("div", { class: "toast" + (bad ? " bad" : ""), role: "status" }, el("i"), el("span", { text: message }));
    if (onClick) { node.style.cursor = "pointer"; node.addEventListener("click", onClick); }
    $("#toasts").append(node);
    setTimeout(() => { node.classList.add("out"); setTimeout(() => node.remove(), 320); }, bad ? 6000 : 3600);
  }

  function confirmBox(title, text, okText = "Delete") {
    return new Promise((resolve) => {
      const close = (answer) => { box.remove(); document.removeEventListener("keydown", onKey); resolve(answer); };
      const onKey = (e) => { if (e.key === "Escape") close(false); };
      const ok = el("button", { class: "btn danger", onclick: () => close(true) }, icon("trash"), okText);
      const box = el("div", { class: "confirm", onclick: (e) => { if (e.target === box) close(false); } },
        el("div", { class: "box", role: "alertdialog", "aria-label": title },
          el("h3", { text: title }), el("p", { text }),
          el("div", { class: "row" }, el("button", { class: "btn ghost", onclick: () => close(false) }, "Keep it"), ok)));
      document.body.append(box);
      document.addEventListener("keydown", onKey);
      ok.focus();
    });
  }

  const fmtBytes = (n) => { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB"]; const i = Math.min(3, Math.floor(Math.log(n) / Math.log(1024))); return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`; };
  function ago(seconds) {
    const d = Date.now() / 1000 - seconds;
    if (d < 45) return "just now";
    if (d < 3600) return `${Math.round(d / 60)} min ago`;
    if (d < 86400) return `${Math.round(d / 3600)} h ago`;
    if (d < 86400 * 7) return `${Math.round(d / 86400)} d ago`;
    return new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }
  const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString(undefined, { hour12: false });
  /** CSS colour tokens for canvases, re-read after a theme switch. */
  let tokenCache = null;
  function tokens() {
    const theme = document.documentElement.dataset.theme || "auto";
    if (!tokenCache || tokenCache.theme !== theme) {
      const css = getComputedStyle(document.documentElement), v = (n) => css.getPropertyValue(n).trim();
      tokenCache = { theme, line: v("--line-2"), lime: v("--lime"), coral: v("--coral"), amber: v("--amber"), ink: v("--ink"), ink3: v("--ink-3"), bg: v("--panel"), tide: v("--tide") };
    }
    return tokenCache;
  }
  function hue(text) { let h = 0; for (const c of String(text)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
  function mark(site) {
    const h = hue(site);
    return el("span", { class: "mono-mark", style: `background:hsl(${h} 70% 72%)`, text: (String(site).replace(/^www\./, "")[0] || "?").toUpperCase() });
  }

  function countUp(node, to, { suffix = "", decimals = 0 } = {}) {
    if (reduced) { node.firstChild.textContent = nf.format(to); return; }
    const start = performance.now(), dur = 1100;
    const step = (now) => {
      const p = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - p, 4);
      node.firstChild.textContent = decimals ? (to * e).toFixed(decimals) : nf.format(Math.round(to * e));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  const store = {
    get(key, fallback) { try { const v = localStorage.getItem("seine." + key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem("seine." + key, JSON.stringify(value)); } catch (e) { /* private window */ } },
  };
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { toast("Could not copy", true); }
  }

  /** Segmented control with a sliding thumb. */
  function seg(options, value, onChange) {
    const thumb = el("span", { class: "thumb" });
    const box = el("div", { class: "seg", role: "radiogroup" }, thumb);
    const move = () => { const on = $("button.on", box); if (on) { thumb.style.width = on.offsetWidth + "px"; thumb.style.transform = `translateX(${on.offsetLeft - 4}px)`; thumb.style.left = "4px"; } };
    for (const [key, label] of options) {
      box.append(el("button", { type: "button", class: key === value ? "on" : "", role: "radio", "aria-checked": String(key === value), text: label,
        onclick: (e) => { $$("button", box).forEach((b) => { b.classList.toggle("on", b === e.currentTarget); b.setAttribute("aria-checked", String(b === e.currentTarget)); }); move(); onChange(key); } }));
    }
    requestAnimationFrame(() => requestAnimationFrame(move));
    return box;
  }

  function tabs(items, active, onChange) {
    const line = el("span", { class: "line" });
    const box = el("div", { class: "tabs", role: "tablist" });
    const move = () => { const on = $("button.on", box); if (on) { line.style.width = on.offsetWidth + "px"; line.style.transform = `translateX(${on.offsetLeft}px)`; } };
    for (const item of items) {
      box.append(el("button", { class: item.key === active ? "on" : "", role: "tab",
        onclick: (e) => { $$("button", box).forEach((b) => b.classList.toggle("on", b === e.currentTarget)); move(); onChange(item.key); } },
        item.label, item.note != null ? el("small", { text: item.note }) : null));
    }
    box.append(line);
    requestAnimationFrame(() => requestAnimationFrame(move));
    return box;
  }

  // ripple on every button
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest(".btn");
    if (!btn || reduced) return;
    const r = btn.getBoundingClientRect(), size = Math.max(r.width, r.height);
    const dot = el("span", { class: "ripple", style: `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px` });
    btn.append(dot);
    setTimeout(() => dot.remove(), 650);
  });

  // ---------------------------------------------------------------- state
  const state = { runs: null, root: "", jobs: [], seenStatus: {} };
  async function loadRuns(force = false) {
    if (!state.runs || force) { const data = await api("/api/runs"); state.runs = data.runs; state.root = data.root; }
    return state.runs;
  }

  // ---------------------------------------------------------------- router
  let routeToken = 0, cleanup = null;
  const stage = $("#stage"), view = $("#view");
  stage.addEventListener("scroll", () => stage.classList.toggle("scrolled", stage.scrollTop > 4), { passive: true });

  function setNav(name) {
    const links = $$("#nav a");
    let active = null;
    links.forEach((a) => { const on = a.dataset.view === name; a.classList.toggle("active", on); if (on) active = a; });
    const glow = $("#navGlow");
    if (active) { glow.style.opacity = 1; glow.style.transform = `translateY(${active.offsetTop}px)`; glow.style.height = active.offsetHeight + "px"; }
    else glow.style.opacity = 0;
  }
  function crumbs(...parts) {
    $("#crumbs").replaceChildren(...parts.flatMap((p, i) => [i ? el("span", { text: "/" }) : null, i === parts.length - 1 ? el("b", { text: p }) : el("span", { text: p })]).filter(Boolean));
  }

  async function route() {
    const token = ++routeToken;
    const [path, qs] = (location.hash.slice(1) || "/").split("?");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    const name = VIEWS[parts[0]] ? parts[0] : "overview";
    if (cleanup) { try { cleanup(); } catch (e) { /* view already gone */ } cleanup = null; }
    setNav(name === "run" ? "library" : name);
    if (view.children.length && !reduced) { [...view.children].forEach((c) => c.classList.add("leaving")); await wait(160); }
    if (token !== routeToken) return;
    view.replaceChildren();
    stage.scrollTop = 0;
    const ctx = { alive: () => token === routeToken, params: new URLSearchParams(qs || "") };
    try {
      const result = await VIEWS[name](view, parts.slice(1), ctx);
      if (ctx.alive()) cleanup = result || null; else if (result) result();
    } catch (err) {
      if (!ctx.alive()) return;
      view.replaceChildren(el("div", { class: "empty" }, icon("x"), el("h3", { text: "Something went sideways" }), el("p", { text: err.message })));
    }
  }
  const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };
  const runHash = (id, file) => `#/run/${encodeURIComponent(id)}` + (file ? `?file=${encodeURIComponent(file)}` : "");

  // ---------------------------------------------------------------- net background (hero)
  function heroNet(canvas) {
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
  class Radar {
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

  /** What a log event says about its page: ok, refused, error, or nothing (pending). */
  function pageStatus(ev) {
    if (ev.message === "http_request") return ev.status && ev.status < 400 ? "ok" : "error";
    if (/robots|blocked|refus|login|paywall/.test(ev.message)) return "refused";
    if (ev.level === "ERROR" || ev.level === "WARNING") return "error";
    if (ev.message === "parsing_complete") return "ok";
    return "pending";
  }

  // ---------------------------------------------------------------- the spider
  /**
   * The page being crawled, drawn from its real text, with a spider walking it.
   * The spider lowers itself onto each page on a silk thread, darts across it in short
   * bursts the way spiders do, wraps every item it takes (the data flows into its abdomen),
   * taps the links the crawl will follow, then climbs back up its thread to the next page.
   */
  class PageWalk {
    constructor(ui) {
      this.ui = ui; this.c = ui.canvas; this.ctx = this.c.getContext("2d");
      this.pages = new Map(); this.queue = []; this.page = null; this.sheet = null;
      this.plan = []; this.target = null; this.actT = 0; this.mode = "hang";
      this.scroll = 0; this.carried = 0; this.walked = 0; this.finished = false;
      this.particles = []; this.silk = []; this.ripples = [];
      this.burst = 0; this.pause = 0; this.drag = null;
      this.s = { x: 0, y: 0, v: 0, h: Math.PI / 2, glow: 0, legs: [], anchor: null };
      this.ro = new ResizeObserver(() => this.size()); this.ro.observe(this.c);
      this.size();
      this.s.x = this.vw / 2; this.s.y = 46; this.s.anchor = { x: this.vw / 2, y: -10 };
      this.initLegs();
      this.say("Hanging on, waiting for the first page…");
      this.last = performance.now();
      this.raf = requestAnimationFrame((t) => this.frame(t));
    }
    size() {
      const r = this.c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
      this.vw = r.width; this.vh = r.height; this.c.width = r.width * dpr; this.c.height = r.height * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    say(text) { this.ui.action(text); }
    path(url) { try { const u = new URL(url); return (u.pathname + u.search) || "/"; } catch (e) { return url; } }

    // --- events in
    visit(url, status) {
      let page = this.pages.get(url);
      if (page) { if (status !== "pending") page.status = status; return; }
      page = { url, status, sketch: null, items: 0, given: 0, seen: performance.now() };
      this.pages.set(url, page); this.queue.push(page);
    }
    sketch(url, sketch) {
      if (!this.pages.has(url)) this.visit(url, "ok");
      const page = this.pages.get(url);
      page.sketch = sketch;
      if (page.status === "pending") page.status = "ok";
    }
    collect(url, n) {
      if (!n) return;
      const page = this.pages.get(url);
      if (!page || page.left) {  // it already moved on: straight into the count
        this.carried += n; if (page) page.given += n; this.ui.carried(this.carried);
        if (page) page.items += n;
        return;
      }
      page.items += n;
    }
    finish() { this.finished = true; }

    // --- pages
    mount(page) {
      const sk = page.sketch, sheet = el("div", { class: "sheet in" });
      if (page.status === "refused" || page.status === "error" || !sk) {
        const refused = page.status === "refused", error = page.status === "error";
        sheet.append(el("div", { class: "s-wall" + (refused ? " refused" : error ? " error" : "") },
          el("div", { class: "s-wall-mark", text: refused ? "⊘" : error ? "!" : "∅" }),
          el("h4", { text: refused ? "Not allowed in" : error ? "The page answered with an error" : "Nothing new to read here" }),
          el("p", { text: refused ? "robots.txt or the site's rules keep crawlers off this page, so the spider leaves it alone."
            : error ? "The spider touched it, found nothing usable, and moved on."
            : "Same content as a page already walked, or not a web page." })));
      } else {
        if (sk.nav && sk.nav.length) sheet.append(el("div", { class: "s-nav" }, ...sk.nav.map((n) => el("span", { text: n }))));
        sk.blocks.forEach((b, i) => {
          if (b.k === "img") { sheet.append(el("div", { class: "s-img", "data-i": i }, icon("page"), el("span", { text: b.alt || "image" }))); return; }
          sheet.append(el(b.k === "h" ? "h4" : "div", { class: `s-${b.k}${b.item ? " is-item" : ""}`, "data-i": i },
            ...b.parts.map(([text, href, follow]) => href ? el("span", { class: "s-link" + (follow ? " s-follow" : ""), title: href, text }) : text)));
        });
        if (!sk.blocks.length) sheet.append(el("div", { class: "s-wall" }, el("h4", { text: "No readable text on this page" })));
      }
      const old = this.sheet;
      if (old) { old.classList.add("out"); setTimeout(() => old.remove(), 450); }
      this.ui.sheets.append(sheet);
      this.sheet = sheet; this.scroll = 0;
      this.ui.tab((sk && sk.title) || this.path(page.url));
      this.ui.address(page.url, page.status);
    }
    planFor(page, backlog) {
      const sheet = this.sheet, plan = [];
      const at = (node, kind) => ({ kind, node, x: Math.min(node.offsetLeft + Math.min(node.offsetWidth * .3, 120), this.vw - 60), y: node.offsetTop + node.offsetHeight / 2 });
      if (!page.sketch || page.status !== "ok") {
        const wall = $(".s-wall", sheet);
        if (wall) plan.push({ ...at(wall, page.status === "ok" ? "look" : "poke"), x: this.vw / 2 });
        return plan;
      }
      const room = Math.max(1, Math.min(6, 6 - Math.floor(backlog * 1.2)));
      let items = $$(".is-item", sheet);
      if (!items.length && page.items) items = $$(".s-h, .s-p, .s-li", sheet).slice(0, 1);
      const pick = (list, n) => list.length <= n ? list : Array.from({ length: n }, (_, k) => list[Math.round(k * (list.length - 1) / Math.max(1, n - 1))]);
      for (const node of pick(items, room)) plan.push(at(node, "item"));
      for (const node of pick($$(".s-follow", sheet), items.length ? Math.min(2, room) : Math.min(4, room + 1))) {
        plan.push({ kind: "link", node, x: node.offsetLeft + node.offsetWidth / 2, y: node.offsetTop + node.offsetHeight / 2 + 2 });
      }
      if (!plan.length) { const first = $("[data-i]", sheet); if (first) plan.push(at(first, "look")); }
      return plan.sort((a, b) => a.y - b.y);
    }

    // --- legs: two alternating groups of four, two-bone IK each
    initLegs() {
      const S = this.S = 1;
      const rest = [[30, 23], [12, 33], [-8, 33], [-26, 26]];  // legs 1-2 reach forward, 3-4 back
      this.s.legs = [];
      for (const side of [-1, 1]) rest.forEach(([f, l], i) => {
        const leg = { side, i, f: f * S, l: l * side * S, hipF: [5, 2.5, 0, -2.5][i] * S, hipL: 3 * side * S,
          l1: 21 * S, l2: 26 * S, group: (i + (side > 0 ? 1 : 0)) % 2, stepping: false, t: 0 };
        const p = this.local(leg.f, leg.l); leg.x = p.x; leg.y = p.y;
        this.s.legs.push(leg);
      });
    }
    local(f, l) { const s = this.s, c = Math.cos(s.h), n = Math.sin(s.h); return { x: s.x + c * f - n * l, y: s.y + n * f + c * l }; }
    get onThread() { return !["walk", "act"].includes(this.mode); }
    updateLegs(dt, now) {
      const sp = this.s;
      if (this.onThread) {  // dangling: legs drawn in, slowly paddling
        for (const leg of sp.legs) {
          const paddle = Math.sin(now / 260 + leg.i * 1.7 + leg.side) * 3;
          const p = this.local(leg.f * .85 + (leg.i < 2 ? 8 : -4) + paddle, leg.l * .82);
          leg.x += (p.x - leg.x) * .35; leg.y += (p.y - leg.y) * .35; leg.stepping = false;
        }
        return;
      }
      const speed = Math.abs(sp.v), busy = [0, 0];
      for (const leg of sp.legs) if (leg.stepping) busy[leg.group]++;
      const dx = Math.cos(sp.h), dy = Math.sin(sp.h), lead = Math.min(14, speed * .09);
      for (const leg of sp.legs) {
        let ideal = this.local(leg.f, leg.l);
        if (this.mode === "act" && leg.i === 0) {  // front legs reach out and feel the prey
          const tap = Math.sin(now / 90 + (leg.side > 0 ? 1.4 : 0));
          ideal = this.local(leg.f + 10 + tap * 5, leg.l * .55);
          leg.x += (ideal.x - leg.x) * .4; leg.y += (ideal.y - leg.y) * .4; leg.stepping = false;
          continue;
        }
        if (leg.stepping) {
          leg.t = Math.min(1, leg.t + dt / leg.dur);
          const e = leg.t < .5 ? 2 * leg.t * leg.t : 1 - Math.pow(-2 * leg.t + 2, 2) / 2;
          leg.x = leg.fx + (leg.tx - leg.fx) * e; leg.y = leg.fy + (leg.ty - leg.fy) * e;
          if (leg.t >= 1) leg.stepping = false;
          continue;
        }
        if (Math.hypot(ideal.x - leg.x, ideal.y - leg.y) > 12 * this.S && !busy[1 - leg.group]) {
          leg.stepping = true; leg.t = 0; leg.dur = Math.max(.055, .12 - speed / 3000);
          leg.fx = leg.x; leg.fy = leg.y; leg.tx = ideal.x + dx * lead; leg.ty = ideal.y + dy * lead;
          busy[leg.group]++;
        }
      }
    }
    knee(hx, hy, fx, fy, l1, l2) {
      let dx = fx - hx, dy = fy - hy, d = Math.hypot(dx, dy) || .001;
      if (d > l1 + l2 - .5) { const k = (l1 + l2 - .5) / d; fx = hx + dx * k; fy = hy + dy * k; d = l1 + l2 - .5; dx = fx - hx; dy = fy - hy; }
      const a = Math.atan2(dy, dx), b = Math.acos(Math.max(-1, Math.min(1, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))));
      const k1 = { x: hx + Math.cos(a + b) * l1, y: hy + Math.sin(a + b) * l1 }, k2 = { x: hx + Math.cos(a - b) * l1, y: hy + Math.sin(a - b) * l1 };
      const away = (k) => Math.hypot(k.x - this.s.x, k.y - this.s.y);  // knees arch away from the body
      return { knee: away(k1) > away(k2) ? k1 : k2, fx, fy };
    }

    // --- behaviour
    next() {
      const backlog = this.queue.length;
      if (backlog > 12) for (const p of this.queue.splice(0, backlog - 4)) this.skip(p);  // catching up on a replay
      const page = this.queue[0];
      if (!page) return false;
      if (page.status !== "refused" && page.status !== "error" && !page.sketch && performance.now() - page.seen < 2500) {
        this.say(`Reading ${this.path(page.url)}…`); return false;
      }
      this.queue.shift();
      this.page = page; this.walked++;
      this.mount(page);
      this.plan = this.planFor(page, this.queue.length);
      this.wraps = this.plan.filter((p) => p.kind === "item").length;
      const first = this.plan[0], sp = this.s;
      sp.x = first ? Math.max(40, Math.min(this.vw - 40, first.x + (Math.random() - .5) * 60)) : this.vw / 2;
      sp.anchor = { x: sp.x, y: -10 }; sp.y = -40; sp.h = Math.PI / 2; sp.v = 0;
      this.landY = Math.max(40, first ? Math.min(first.y - 30, this.vh * .5) : 90);
      this.mode = "descend";
      this.say(page.status === "refused" ? `Lowering onto ${this.path(page.url)} — off limits` : `Dropping onto ${this.path(page.url)}`);
      return true;
    }
    skip(page) {
      page.left = true;
      if (page.items > page.given) { this.carried += page.items - page.given; page.given = page.items; this.ui.carried(this.carried); }
      this.walked++;
    }
    leave() {
      const page = this.page;
      if (page) { page.left = true; if (page.items > page.given) this.spray(this.s.x, this.s.y, page.items - page.given, page); }
      this.page = null;
    }
    spray(x, y, n, page) {
      if (!n) return;
      page.given += n;
      const count = Math.max(3, Math.min(12, Math.round(Math.sqrt(n) * 2.2)));
      let left = n;
      for (let i = 0; i < count; i++) {
        const value = i === count - 1 ? left : Math.floor(n / count); left -= value;
        this.particles.push({ x0: x + (Math.random() - .5) * 60, y0: y + (Math.random() - .5) * 14, t: -i * .05, dur: .5 + Math.random() * .3, bend: (Math.random() - .5) * 70, value });
      }
    }

    update(dt, now) {
      const sp = this.s, hurry = 1 + Math.min(4, this.queue.length * .4);
      if (this.mode === "hang" || this.mode === "rest") {
        if (this.queue.length && this.mode === "rest") this.mode = "ascend";
        else if (this.queue.length && !this.next()) { /* waiting for the page's text */ }
        if (this.mode === "hang" || this.mode === "rest") {
          sp.x = sp.anchor.x + Math.sin(now / 900) * 6;
          const restY = (this.mode === "rest" ? this.vh * .42 : 46) + this.scroll;
          sp.y += (restY - sp.y) * Math.min(1, dt * 2);
          sp.h = Math.PI / 2 + Math.sin(now / 900) * .08;
          if (!this.queue.length && this.finished && this.mode === "hang") {
            this.mode = "rest"; this.say(`Done · ${nf.format(this.walked)} pages walked · ${nf.format(this.carried)} items carried home`);
          }
        }
      } else if (this.mode === "descend") {
        sp.y += 260 * hurry * dt; sp.x = sp.anchor.x + Math.sin(now / 400) * 2; sp.h = Math.PI / 2;
        if (sp.y >= this.landY + this.scroll) {
          this.mode = this.plan.length ? "walk" : "ascend";
          this.drag = { x: sp.x, y: sp.y };  // its dragline stays fixed where it landed
          this.burst = 0; this.pause = .15;
          if (this.mode === "ascend") sp.anchor = { x: sp.x, y: -10 };
        }
      } else if (this.mode === "walk") {
        const t = this.plan[0], dx = t.x - sp.x, dy = t.y - sp.y, d = Math.hypot(dx, dy);
        const want = Math.atan2(dy, dx), diff = Math.atan2(Math.sin(want - sp.h), Math.cos(want - sp.h));
        let speed = 0;
        if (Math.abs(diff) > .9 && d > 6) {
          sp.h += Math.sign(diff) * Math.min(Math.abs(diff), 7 * dt);  // stop and pivot on the spot first
        } else {
          sp.h += diff * Math.min(1, dt * 10);
          if (d < 46) speed = Math.max(30, d * 5) * hurry;
          else if (this.pause > 0) this.pause -= dt * hurry;
          else if (this.burst > 0) { this.burst -= dt; speed = 210 * hurry; }
          else if (Math.random() < .35) this.pause = .07 + Math.random() * .25;  // the stop-start of a hunting spider
          else this.burst = .22 + Math.random() * .4;
          speed = Math.min(speed, d * 8) * Math.pow(Math.max(0, Math.cos(diff)), 2);  // slow while turning, or it circles the target
        }
        sp.v += (speed - sp.v) * Math.min(1, dt * 16);
        sp.x += Math.cos(sp.h) * sp.v * dt; sp.y += Math.sin(sp.h) * sp.v * dt;
        if (d < 6) {
          this.mode = "act"; this.actT = 0; sp.v = 0; this.target = t;
          this.say(t.kind === "item" ? "Wrapping an item" : t.kind === "link" ? `Tapping a link: ${t.node.textContent.trim().slice(0, 40)}`
            : t.kind === "poke" ? "Touched it — backing off" : "Feeling around the page");
        }
      } else if (this.mode === "act") {
        const t = this.target, length = { item: 1.05, link: .55, poke: .6, look: .45 }[t.kind] / hurry;
        this.actT += dt;
        if (t.kind === "item" && Math.random() < dt * 22) {  // silk thrown over the prey
          this.silk.push({ x: t.node.offsetLeft + Math.random() * t.node.offsetWidth, y: t.node.offsetTop + Math.random() * t.node.offsetHeight, life: 1 });
        }
        if (this.actT > length * .5 && !t.done) {
          t.done = true;
          if (t.kind === "item") {
            t.node.classList.add("caught");
            const share = this.wraps > 0 ? Math.round((this.page.items - this.page.given) / this.wraps) : 0;
            this.wraps--;
            this.spray(t.x, t.y, Math.max(0, share), this.page);
          } else if (t.kind === "link") {
            t.node.classList.add("tapped"); this.ripples.push({ x: t.x, y: t.y, t: 0 });
          } else if (t.kind === "poke") {
            t.node.classList.add("shake"); sp.v = -160;
          }
        }
        if (t.kind === "poke") { sp.x += Math.cos(sp.h) * sp.v * dt; sp.y += Math.sin(sp.h) * sp.v * dt; sp.v *= .9; }
        if (this.actT >= length) {
          this.plan.shift(); this.mode = this.plan.length ? "walk" : "ascend"; this.burst = 0; this.pause = .1;
          if (this.mode === "ascend") { sp.anchor = { x: sp.x, y: -10 }; this.say(this.queue.length ? "Climbing up to the next page" : "Climbing back up the thread"); }
        }
      } else if (this.mode === "ascend") {
        const up = -Math.PI / 2, diff = Math.atan2(Math.sin(up - sp.h), Math.cos(up - sp.h));
        sp.h += diff * Math.min(1, dt * 10);
        sp.y -= 300 * hurry * dt;
        if (sp.y < this.scroll - 50) {
          this.leave(); this.drag = null;
          sp.anchor = { x: sp.x, y: -10 }; sp.h = Math.PI / 2;
          this.mode = "hang"; sp.y = this.scroll - 50;
          if (!this.next() && !this.finished) this.say("Listening for the next page…");
        }
      }
      // the camera follows the spider down long pages
      if (this.sheet && (this.mode === "walk" || this.mode === "act")) {
        const max = Math.max(0, this.sheet.offsetHeight - this.vh + 40), want = Math.max(0, Math.min(max, sp.y - this.vh * .45));
        this.scroll += (want - this.scroll) * Math.min(1, dt * 3.5);
      }
      if (this.sheet) this.sheet.style.transform = `translateY(${-this.scroll}px)`;
      this.updateLegs(dt, now);
      for (const p of this.particles) p.t += dt / p.dur;
      for (const p of this.particles) if (p.t >= 1 && !p.landed) { p.landed = true; this.carried += p.value; sp.glow = 1; this.ui.carried(this.carried); }
      this.particles = this.particles.filter((p) => p.t < 1);
      for (const s of this.silk) s.life -= dt * .9;
      this.silk = this.silk.filter((s) => s.life > 0);
      for (const r of this.ripples) r.t += dt * 1.8;
      this.ripples = this.ripples.filter((r) => r.t < 1);
      sp.glow = Math.max(0, sp.glow - dt * 1.5);
    }

    // --- drawing
    frame(now) {
      const dt = Math.min(.05, (now - this.last) / 1000); this.last = now;
      this.update(dt, now);
      const { ctx } = this, t = tokens(), sp = this.s;
      ctx.clearRect(0, 0, this.vw, this.vh);
      ctx.save(); ctx.translate(0, -this.scroll);
      const spin = this.local(-19 * this.S, 0);
      ctx.strokeStyle = t.ink; ctx.lineWidth = 1;
      if (!this.onThread) {
        if (this.drag) {  // the dragline: from the top, down to where it landed, then trailing behind it
          ctx.globalAlpha = .18; ctx.beginPath(); ctx.moveTo(this.drag.x, this.scroll - 10); ctx.lineTo(this.drag.x, this.drag.y); ctx.stroke();
          ctx.globalAlpha = .32; ctx.beginPath(); ctx.moveTo(this.drag.x, this.drag.y); ctx.lineTo(spin.x, spin.y); ctx.stroke();
        }
      } else {  // hanging from its thread
        const end = this.mode === "ascend" ? this.local(12, 0) : spin;
        ctx.globalAlpha = .55; ctx.beginPath(); ctx.moveTo(sp.anchor.x, this.scroll - 10);
        ctx.quadraticCurveTo((sp.anchor.x + end.x) / 2 + Math.sin(now / 700) * 4, (this.scroll - 10 + end.y) / 2, end.x, end.y); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      for (const s of this.silk) { ctx.globalAlpha = s.life * .6; ctx.beginPath(); ctx.moveTo(spin.x, spin.y); ctx.lineTo(s.x, s.y); ctx.stroke(); }
      for (const r of this.ripples) { ctx.globalAlpha = 1 - r.t; ctx.strokeStyle = t.tide; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(r.x, r.y, 6 + r.t * 26, 0, 6.283); ctx.stroke(); }
      ctx.globalAlpha = 1;
      this.drawSpider(ctx, t, now);
      const abd = this.local(-10 * this.S, 0);
      for (const p of this.particles) {
        if (p.t < 0) continue;
        const e = 1 - Math.pow(1 - p.t, 3), mx = (p.x0 + abd.x) / 2 + p.bend, my = (p.y0 + abd.y) / 2 - 36;
        const x = (1 - e) * (1 - e) * p.x0 + 2 * (1 - e) * e * mx + e * e * abd.x, y = (1 - e) * (1 - e) * p.y0 + 2 * (1 - e) * e * my + e * e * abd.y;
        ctx.fillStyle = t.lime; ctx.globalAlpha = 1 - p.t * .3; ctx.fillRect(x - 2, y - 2, 4, 4);
      }
      ctx.restore(); ctx.globalAlpha = 1;
      this.raf = requestAnimationFrame((n) => this.frame(n));
    }
    drawSpider(ctx, t, now) {
      const sp = this.s, S = this.S, moving = Math.abs(sp.v) > 12;
      const bob = moving ? Math.sin(now / 45) * .6 : Math.sin(now / 650) * .35;
      if (!this.onThread) { ctx.globalAlpha = .25; ctx.fillStyle = "#000"; ctx.beginPath(); ctx.ellipse(sp.x + 3, sp.y + 6, 14 * S, 8 * S, sp.h, 0, 6.283); ctx.fill(); ctx.globalAlpha = 1; }
      ctx.strokeStyle = t.ink; ctx.fillStyle = t.ink; ctx.lineCap = "round"; ctx.lineJoin = "round";
      for (const leg of sp.legs) {
        const hip = this.local(leg.hipF, leg.hipL);
        let { knee, fx, fy } = this.knee(hip.x, hip.y, leg.x, leg.y, leg.l1, leg.l2);
        if (leg.stepping) {  // the knee rises as the foot swings
          const lift = Math.sin(leg.t * Math.PI) * 5 * S, d = Math.hypot(knee.x - sp.x, knee.y - sp.y) || 1;
          knee = { x: knee.x + (knee.x - sp.x) / d * lift, y: knee.y + (knee.y - sp.y) / d * lift };
        }
        ctx.lineWidth = 2.6 * S; ctx.beginPath(); ctx.moveTo(hip.x, hip.y); ctx.lineTo(knee.x, knee.y); ctx.stroke();
        ctx.lineWidth = 1.5 * S; ctx.beginPath(); ctx.moveTo(knee.x, knee.y); ctx.lineTo(fx, fy); ctx.stroke();
        ctx.beginPath(); ctx.arc(knee.x, knee.y, 1.4 * S, 0, 6.283); ctx.fill();
      }
      ctx.save(); ctx.translate(sp.x, sp.y); ctx.rotate(sp.h);
      const palp = this.mode === "act" ? Math.sin(now / 70) * 1.6 : Math.sin(now / 500) * .6;  // pedipalps twitch while it works
      ctx.lineWidth = 1.5 * S; ctx.beginPath();
      ctx.moveTo(7 * S, -2 * S); ctx.lineTo(12 * S, (-3.5 - palp) * S); ctx.moveTo(7 * S, 2 * S); ctx.lineTo(12 * S, (3.5 + palp) * S); ctx.stroke();
      ctx.shadowColor = t.lime; ctx.shadowBlur = 22 * sp.glow;  // the abdomen glows as data flows in
      ctx.beginPath(); ctx.ellipse(-10 * S, 0, (9.5 + bob) * S, (7.4 + bob * .6) * S, 0, 0, 6.283); ctx.fill();
      ctx.shadowBlur = 0; ctx.fillStyle = t.lime; ctx.globalAlpha = .55 + sp.glow * .45;
      for (const [x, w] of [[-6, 2.2], [-10, 3], [-14, 2.1]]) { ctx.beginPath(); ctx.moveTo((x + 1.6) * S, 0); ctx.lineTo((x - 1.4) * S, -w * S); ctx.lineTo((x - .4) * S, 0); ctx.lineTo((x - 1.4) * S, w * S); ctx.closePath(); ctx.fill(); }
      ctx.globalAlpha = 1; ctx.fillStyle = t.ink; ctx.beginPath(); ctx.ellipse(2 * S, 0, 6.4 * S, 5.4 * S, 0, 0, 6.283); ctx.fill();
      ctx.fillStyle = t.bg;
      for (const [x, y, r] of [[6, -1.5, 1.1], [6, 1.5, 1.1], [4.6, -3, .65], [4.6, 3, .65]]) { ctx.beginPath(); ctx.arc(x * S, y * S, r * S, 0, 6.283); ctx.fill(); }
      ctx.restore();
    }
    destroy() { cancelAnimationFrame(this.raf); this.ro.disconnect(); }
  }

  // ---------------------------------------------------------------- run card
  function runCard(run, index = 0) {
    const formats = [...new Set(run.files.map((f) => f.format))];
    const reveal = el("button", { class: "icon-btn", title: "Open folder", "aria-label": "Open folder", onclick: async (e) => { e.stopPropagation(); try { await api("/api/reveal", { method: "POST", query: { path: run.id } }); } catch (err) { toast(err.message, true); } } }, icon("folder"));
    const del = el("button", { class: "icon-btn", title: "Delete run", "aria-label": "Delete run", onclick: async (e) => { e.stopPropagation(); await deleteRun(run, card); } }, icon("trash"));
    const card = el("article", { class: "run", tabindex: 0, style: `animation-delay:${Math.min(index, 14) * 40}ms`,
      onclick: () => go(runHash(run.id)), onkeydown: (e) => { if (e.key === "Enter") go(runHash(run.id)); } },
      el("div", { class: "top" }, mark(run.site), el("div", {}, el("div", { class: "site", text: run.site }), el("div", { class: "when", text: `${ago(run.created)} · ${run.name}` }))),
      el("div", { class: "count", text: run.items == null ? "—" : nf.format(run.items) }, el("small", { text: run.items === 1 ? "item" : "items" })),
      el("div", { class: "foot" }, ...formats.map((f) => el("span", { class: "fmt " + f, text: f })), el("span", { class: "size", text: fmtBytes(run.size) })),
      el("div", { class: "acts" }, reveal, del));
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", px * 100 + "%"); card.style.setProperty("--my", py * 100 + "%");
      if (!reduced && !card.parentElement?.classList.contains("list")) card.style.transform = `perspective(900px) rotateX(${(.5 - py) * 5}deg) rotateY(${(px - .5) * 6}deg) translateY(-3px)`;
    });
    card.addEventListener("pointerleave", () => { card.style.transform = ""; });
    return card;
  }

  async function deleteRun(run, node) {
    if (!(await confirmBox("Delete this run?", `${run.name} — ${run.files.length} file(s), ${fmtBytes(run.size)}. The files are removed from disk.`))) return false;
    try {
      await api("/api/runs", { method: "DELETE", query: { path: run.id } });
      state.runs = (state.runs || []).filter((r) => r.id !== run.id);
      if (node) { node.style.transition = "all .4s cubic-bezier(.22,1,.36,1)"; node.style.opacity = 0; node.style.transform = "scale(.9)"; setTimeout(() => node.remove(), 400); }
      toast("Run deleted");
      return true;
    } catch (err) { toast(err.message, true); return false; }
  }

  // ---------------------------------------------------------------- views
  const VIEWS = {};

  VIEWS.overview = async (root, _parts, ctx) => {
    crumbs("Seine", "Overview");
    const input = el("input", { placeholder: "https://any-public-site.com/page", "aria-label": "Address to extract from", type: "url" });
    const launch = (mode) => {
      const url = input.value.trim();
      if (!/^https?:\/\//i.test(url)) { input.focus(); toast("Start with http:// or https://", true); return; }
      const form = store.get("form", {}); form.urls = url; store.set("form", form);
      go("#/extract" + (mode === "quick" ? "?look=1" : ""));
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") launch(e.shiftKey ? "quick" : "crawl"); });
    const canvas = el("canvas", { "aria-hidden": "true" });
    const words = ["What", "shall", "we"].flatMap((w, i) => [el("span", { class: "word", style: `animation-delay:${i * 70}ms`, text: w }), " "]);
    const hero = el("section", { class: "hero" }, canvas,
      el("div", { class: "hero-inner" },
        el("div", { class: "eyebrow", text: `${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })} · local studio` }),
        el("h1", {}, ...words, el("span", { class: "word", style: "animation-delay:210ms" }, el("em", { text: "haul in" })), " ", el("span", { class: "word", style: "animation-delay:280ms", text: "today?" })),
        el("div", { class: "haul" }, input,
          el("button", { class: "btn ghost", onclick: () => launch("quick"), title: "One page, nothing saved (Shift+Enter)" }, icon("eye"), "Look"),
          el("button", { class: "btn primary", onclick: () => launch("crawl") }, "Cast the net", icon("arrow"))),
        el("div", { class: "quick" }, el("span", { text: "Try" }),
          ...["https://books.toscrape.com/", "https://quotes.toscrape.com/", "https://web-scraping.dev/products"].map((u) =>
            el("button", { text: u.replace(/^https:\/\//, ""), onclick: () => { input.value = u; input.focus(); } })))));
    root.append(hero);
    const stopNet = heroNet(canvas);

    const tiles = el("section", { class: "grid cols-4" }, ...[0, 1, 2, 3].map(() => el("div", { class: "card stat skeleton", style: "height:128px" })));
    const lower = el("section", { class: "grid cols-main", style: "margin-top:18px" }, el("div", { class: "card skeleton", style: "height:300px" }), el("div", { class: "card skeleton", style: "height:300px" }));
    const recentWrap = el("section", { style: "margin-top:30px" });
    root.append(tiles, lower, recentWrap);

    const stats = await api("/api/stats");
    if (!ctx.alive()) return stopNet;
    const days = stats.days;
    const spark = (key) => {
      const vals = days.map((d) => d[key]), max = Math.max(1, ...vals);
      const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 120},${56 - (v / max) * 48}`);
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 120 60"); svg.setAttribute("class", "spark"); svg.setAttribute("preserveAspectRatio", "none");
      svg.innerHTML = `<path class="area" d="M0,60 L${pts.join(" L")} L120,60Z"/><path d="M${pts.join(" L")}"/>`;  // numbers only
      return svg;
    };
    const tile = (label, value, key, suffix) => {
      const num = el("div", { class: "num" }, "0", suffix ? el("small", { text: suffix }) : null);
      const node = el("div", { class: "card stat" }, el("div", { class: "eyebrow", text: label }), num, key ? spark(key) : null);
      countUp(num, value);
      return node;
    };
    const sizeVal = stats.size / 1024 / 1024;
    const sizeTile = el("div", { class: "card stat" }, el("div", { class: "eyebrow", text: "On disk" }), el("div", { class: "num" }, "0", el("small", { text: "MB" })));
    countUp($(".num", sizeTile), sizeVal, { decimals: 1 });
    tiles.replaceChildren(tile("Items harvested", stats.items, "items"), tile("Runs", stats.runs, "runs"), tile("Sites", stats.sites, null), sizeTile);

    // 30-day bars
    const chartBox = el("div", { class: "chart" });
    const tip = el("div", { class: "tip" });
    const W = 600, H = 200, max = Math.max(1, ...days.map((d) => d.items)), bw = W / days.length;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H + 22}`); svg.setAttribute("preserveAspectRatio", "none");
    let markup = "";
    for (const f of [.25, .5, .75, 1]) markup += `<line class="gridline" x1="0" x2="${W}" y1="${H - f * H}" y2="${H - f * H}"/>`;
    days.forEach((d, i) => {
      const bh = d.items ? Math.max(4, (d.items / max) * (H - 10)) : 2;
      markup += `<g class="col" data-i="${i}"><rect class="bar ${d.items ? "has" : ""} ${i === days.length - 1 ? "today" : ""}" x="${i * bw + 3}" y="${H - bh}" width="${bw - 6}" height="${bh}" style="animation-delay:${i * 22}ms"/><rect class="hit" x="${i * bw}" y="0" width="${bw}" height="${H}"/></g>`;
    });
    [0, 14, 29].forEach((i) => { markup += `<text class="axis" x="${i * bw + bw / 2}" y="${H + 18}" text-anchor="middle">${new Date(days[i].day + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }).replace(/[<>&]/g, "")}</text>`; });
    svg.innerHTML = markup;  // numbers and locale date strings only
    svg.addEventListener("pointermove", (e) => {
      const g = e.target.closest(".col"); if (!g) { tip.classList.remove("on"); return; }
      const d = days[+g.dataset.i], r = chartBox.getBoundingClientRect(), br = g.getBoundingClientRect();
      tip.textContent = `${new Date(d.day + "T00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} · ${nf.format(d.items)} items · ${d.runs} runs`;
      tip.style.left = br.left - r.left + br.width / 2 + "px"; tip.style.top = (br.top - r.top) + "px"; tip.classList.add("on");
    });
    svg.addEventListener("pointerleave", () => tip.classList.remove("on"));
    chartBox.append(svg, tip);
    const total30 = days.reduce((s, d) => s + d.items, 0);

    const topMax = Math.max(1, ...stats.top_sites.map(([, n]) => n));
    const sites = el("ul", { class: "sites" }, ...stats.top_sites.map(([site, n], i) => el("li", { onclick: () => go(`#/library?site=${encodeURIComponent(site)}`) },
      mark(site), el("span", { text: site }), el("b", { text: nf.format(n) }),
      el("div", { class: "meter" }, el("i", { style: `width:${(n / topMax) * 100}%;animation-delay:${200 + i * 90}ms` })))));
    lower.replaceChildren(
      el("div", { class: "card" }, el("h2", {}, "Catch, last 30 days", el("span", { class: "eyebrow", text: `${nf.format(total30)} items` })), chartBox),
      el("div", { class: "card" }, el("h2", {}, "Richest waters", el("span", { class: "eyebrow", text: "items by site" })),
        stats.top_sites.length ? sites : el("p", { class: "hint", text: "Nothing caught yet." })));

    recentWrap.append(el("div", { class: "page-head", style: "margin:0 0 16px" }, el("h2", { style: "font:400 34px var(--serif)", text: "Fresh off the boat" }),
      el("div", { class: "actions" }, el("a", { class: "btn ghost small", href: "#/library" }, "All runs", icon("arrow")))));
    recentWrap.append(stats.recent.length ? el("div", { class: "runs" }, ...stats.recent.map((r, i) => runCard(r, i)))
      : el("div", { class: "empty card" }, icon("net"), el("h3", { text: "The net is empty" }), el("p", { text: "Paste an address above to make your first haul." })));
    return stopNet;
  };

  // ---------------------------------------------------------------- extract
  const DEFAULT_FORM = { urls: "", recipe: "auto", max_pages: 50, max_depth: 3, rate_limit: 1, mode: "auto", export: ["jsonl", "csv"],
    sitemap: false, discover: false, pagination: true, markdown: false, track: false, incremental: false, open_web: false,
    include: "", exclude: "", item: "", examples: [["title", ""], ["price", ""]], selectors: [["title", "h1::text"]] };

  VIEWS.extract = async (root, _parts, ctx) => {
    crumbs("Seine", "Extract");
    const f = Object.assign(structuredClone(DEFAULT_FORM), store.get("form", {}));
    const save = () => { store.set("form", f); renderCmd(); };

    const urls = el("textarea", { class: "input", rows: 2, placeholder: "https://example.com/catalogue\nOne address per line — every one is a starting point", value: f.urls,
      oninput: (e) => { f.urls = e.target.value; save(); } });
    const recipeDefs = [
      ["auto", "sparkles", "Auto-detect", "Finds lists, products, articles and tables by itself."],
      ["examples", "wand", "Teach by example", "Type values you can see. It learns the selectors."],
      ["selectors", "code", "CSS selectors", "Exact fields with CSS or XPath, per item."],
      ["quick", "eye", "Quick look", "One page, nothing saved. See what's there."],
    ];
    const fieldsBox = el("div");
    const recipes = el("div", { class: "recipes" }, ...recipeDefs.map(([key, ic, title, text]) => el("button", { type: "button", class: "recipe" + (f.recipe === key ? " on" : ""),
      onclick: (e) => { f.recipe = key; $$(".recipe", recipes).forEach((r) => r.classList.toggle("on", r === e.currentTarget)); renderFields(); renderLaunch(); save(); } },
      icon(ic), el("b", { text: title }), el("small", { text }))));

    function pairRows(list, namePh, valuePh) {
      const box = el("div", { class: "pairs" });
      const draw = () => {
        box.replaceChildren(...list.map((pair, i) => el("div", { class: "pair" },
          el("input", { class: "input", value: pair[0], placeholder: namePh, "aria-label": "Field name", oninput: (e) => { pair[0] = e.target.value; save(); } }),
          el("input", { class: "input", value: pair[1], placeholder: valuePh, "aria-label": "Field value", oninput: (e) => { pair[1] = e.target.value; save(); } }),
          el("button", { class: "icon-btn", type: "button", "aria-label": "Remove field", onclick: (e) => { const row = e.currentTarget.parentElement; row.classList.add("out"); setTimeout(() => { list.splice(i, 1); draw(); save(); }, 180); } }, icon("x")))),
          el("div", {}, el("button", { class: "btn ghost small", type: "button", onclick: () => { list.push(["", ""]); draw(); save(); $$("input", box).at(-2)?.focus(); } }, icon("plus"), "Add field")));
      };
      draw();
      return box;
    }
    function renderFields() {
      if (f.recipe === "examples") {
        fieldsBox.replaceChildren(el("p", { class: "hint", style: "margin:14px 0 0", text: "Copy one item's values exactly as they appear on the first page. Example: title = A Light in the Attic, price = £51.77." }),
          pairRows(f.examples, "field name", "value you can see on the page"));
      } else if (f.recipe === "selectors") {
        fieldsBox.replaceChildren(
          el("label", { class: "field", style: "margin-top:14px" }, el("span", { text: "Item selector (optional) — one record per match" }),
            el("input", { class: "input", value: f.item, placeholder: ".product_pod", oninput: (e) => { f.item = e.target.value; save(); } })),
          pairRows(f.selectors, "field name", "h3 a::attr(title)  or  //span[@class='price']/text()"));
      } else fieldsBox.replaceChildren();
    }

    const slider = (key, label, min, max, stepV, fmt = (v) => v) => {
      const out = el("output", { text: fmt(f[key]) });
      const input = el("input", { type: "range", min, max, step: stepV, value: f[key], "aria-label": label });
      const paint = () => input.style.setProperty("--fill", ((input.value - min) / (max - min)) * 100 + "%");
      input.addEventListener("input", () => { f[key] = +input.value; out.textContent = fmt(f[key]); paint(); save(); });
      paint();
      return el("label", { class: "field" }, el("span", {}, label, out), input);
    };
    const sw = (key, title, text) => el("label", { class: "switch" },
      el("input", { type: "checkbox", checked: !!f[key], onchange: (e) => { f[key] = e.target.checked; save(); } }), el("i"),
      el("div", {}, el("b", { text: title }), el("small", { text })));
    const exportChips = el("div", { class: "chips" }, ...["jsonl", "csv", "json", "sqlite"].map((fmt) => el("button", { type: "button", class: "chip" + (f.export.includes(fmt) ? " on" : ""), text: fmt.toUpperCase(),
      onclick: (e) => { const on = !f.export.includes(fmt); if (!on && f.export.length === 1) { toast("Keep at least one format", true); return; } f.export = on ? [...f.export, fmt] : f.export.filter((x) => x !== fmt); e.currentTarget.classList.toggle("on", on); save(); } })));

    const step = (n, title, ...body) => el("section", { class: "card" }, el("h2", {}, el("span", { class: "step-n", text: n }), title), ...body);
    const steps = el("div", { class: "steps" },
      step("01", "Where to cast", el("div", { style: "margin-top:14px" }, urls)),
      step("02", "What to catch", recipes, fieldsBox),
      step("03", "How far to go",
        el("div", { class: "opts" },
          slider("max_pages", "Pages, at most", 1, 500, 1),
          slider("max_depth", "Link depth", 0, 10, 1, (v) => (v === 0 ? "start pages only" : `${v} clicks`)),
          slider("rate_limit", "Pace per site", .2, 5, .1, (v) => `${(+v).toFixed(1)} req/s`),
          el("div", { class: "field" }, el("span", { text: "Rendering" }), seg([["auto", "Auto"], ["browser", "Browser"], ["static", "Plain HTTP"]], f.mode, (v) => { f.mode = v; save(); }))),
        el("div", { class: "switches" },
          sw("pagination", "Follow next pages", "Page 2, 3… don't count against depth"),
          sw("sitemap", "Read sitemaps", "Find pages the site lists for search engines"),
          sw("discover", "Discover feeds & APIs", "RSS, WordPress and Shopify endpoints too"),
          sw("markdown", "Keep page text", "Adds a markdown field for article pages"),
          sw("track", "Track changes", "Compare with the last run of the same crawl"),
          sw("incremental", "Skip unchanged pages", "Uses ETags from earlier runs"),
          sw("open_web", "Roam the open web", "Follow links to other sites (depth = hops)")),
        el("details", { class: "more" }, el("summary", { text: "Link filters" }),
          el("div", { class: "opts" },
            el("label", { class: "field" }, el("span", { text: "Only follow links matching (regex, one per line)" }), el("textarea", { class: "input", rows: 3, value: f.include, placeholder: "/catalogue/\n/category/books", oninput: (e) => { f.include = e.target.value; save(); } })),
            el("label", { class: "field" }, el("span", { text: "Never follow links matching" }), el("textarea", { class: "input", rows: 3, value: f.exclude, placeholder: "\\?sort=\n/cart", oninput: (e) => { f.exclude = e.target.value; save(); } }))))),
      step("04", "Keep it as", el("div", { style: "margin-top:14px" }, exportChips)));
    renderFields();

    // side panel
    const cmd = el("div", { class: "cmd" });
    function payload() {
      const list = (s) => s.split("\n").map((x) => x.trim()).filter(Boolean);
      const pairs = (rows) => Object.fromEntries(rows.filter(([k, v]) => k.trim() && v.trim()).map(([k, v]) => [k.trim(), v.trim()]));
      const p = { urls: list(f.urls), max_pages: f.max_pages, max_depth: f.max_depth, rate_limit: f.rate_limit, mode: f.mode, export: f.export,
        sitemap: f.sitemap, discover: f.discover, markdown: f.markdown, track: f.track, incremental: f.incremental, open_web: f.open_web,
        no_pagination: !f.pagination, include: list(f.include), exclude: list(f.exclude) };
      if (f.recipe === "examples") p.examples = pairs(f.examples);
      if (f.recipe === "selectors") { p.selectors = pairs(f.selectors); if (f.item.trim()) p.item = f.item.trim(); }
      return p;
    }
    function renderCmd() {
      const p = payload(), parts = [["python -m my_scraper.cli.main", null]];
      const q = (v) => (/[\s"'|&<>^]/.test(v) ? `"${v.replace(/"/g, '\\"')}"` : v);
      if (p.urls.length) parts.push(["--urls", p.urls.map(q).join(" ")]);
      parts.push(["--max-pages", p.max_pages], ["--max-depth", p.max_depth]);
      if (p.rate_limit !== 1) parts.push(["--rate-limit", p.rate_limit]);
      if (p.mode === "browser") parts.push(["--use-browser", null]); if (p.mode === "static") parts.push(["--no-browser", null]);
      for (const k of ["sitemap", "discover", "markdown", "track", "incremental", "open_web", "no_pagination"]) if (p[k]) parts.push(["--" + k.replace("_", "-"), null]);
      for (const x of p.include) parts.push(["--include", q(x)]); for (const x of p.exclude) parts.push(["--exclude", q(x)]);
      if (p.item) parts.push(["--item", q(p.item)]);
      for (const [k, v] of Object.entries(p.selectors || {})) parts.push(["--selector", q(`${k}=${v}`)]);
      for (const [k, v] of Object.entries(p.examples || {})) parts.push(["--example", q(`${k}=${v}`)]);
      parts.push(["--export", p.export.join(" ")]);
      cmd.replaceChildren(...parts.flatMap(([k, v], i) => [i ? " " : "", el("span", { class: i ? "k" : "", text: k }), v != null ? " " : "", v != null ? el("span", { class: "v", text: String(v) }) : ""]));
    }
    renderCmd();

    const launchBtn = el("button", { class: "btn primary launch" });
    const lookBtn = el("button", { class: "btn", style: "width:100%" }, icon("eye"), "Quick look at the first page");
    const previewBox = el("div");
    function renderLaunch() {
      launchBtn.replaceChildren(...(f.recipe === "quick" ? [icon("eye"), "Look now"] : [icon("bolt"), "Cast the net"]));
      lookBtn.hidden = f.recipe === "quick";
    }
    renderLaunch();

    async function quickLook() {
      const first = payload().urls[0];
      if (!first) { urls.focus(); toast("Add an address first", true); return; }
      lookBtn.classList.add("busy"); launchBtn.disabled = true;
      previewBox.replaceChildren(el("div", { class: "card skeleton preview", style: "height:260px" }));
      try {
        const data = await api("/v1/scrape", { method: "POST", body: { url: first, render: f.mode === "static" ? "never" : f.mode === "browser" ? "always" : "auto", max_chars: 40000 } });
        if (!ctx.alive()) return;
        previewBox.replaceChildren(previewCard(data));
        previewBox.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
      } catch (err) {
        previewBox.replaceChildren(el("div", { class: "done-banner bad preview" }, el("h3", { text: "Couldn't look" }), el("span", { text: err.message })));
      } finally { lookBtn.classList.remove("busy"); launchBtn.disabled = false; }
    }
    lookBtn.addEventListener("click", quickLook);
    launchBtn.addEventListener("click", async () => {
      if (f.recipe === "quick") return quickLook();
      const p = payload();
      if (!p.urls.length) { urls.focus(); toast("Add at least one address", true); return; }
      launchBtn.classList.add("busy");
      try {
        const job = await api("/api/jobs", { method: "POST", body: p });
        toast("Net cast — watching it live");
        pollJobs();
        go(`#/live/${job.id}`);
      } catch (err) { toast(err.message, true); } finally { launchBtn.classList.remove("busy"); }
    });

    const side = el("aside", { class: "side" },
      el("div", { class: "card" }, el("h2", {}, "Ready when you are"), el("p", { class: "hint", text: "Robots.txt is obeyed, sites are paced, logins and paywalls are never crossed." }),
        el("div", { style: "display:flex;flex-direction:column;gap:10px;margin-top:14px" }, launchBtn, lookBtn)),
      el("div", { class: "card" }, el("h2", {}, "Same thing, from a terminal", el("button", { class: "btn ghost small", style: "margin-left:auto", onclick: () => copy(cmd.textContent) }, icon("copy"), "Copy")),
        el("div", { style: "margin-top:12px" }, cmd)),
      el("button", { class: "btn ghost small", onclick: () => { store.set("form", {}); route(); toast("Form reset"); } }, "Reset the form"));

    root.append(el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "New extraction" }), el("h1", {}, "Cast a ", el("em", { text: "net" })),
      el("p", { text: "Point it at a public site, say what you're after, and let it work. Everything lands in your Library." }))));
    root.append(el("div", { class: "extract" }, el("div", {}, steps, previewBox), side));
    if (ctx.params.get("look") && f.urls.trim()) quickLook();
    else if (!f.urls) urls.focus();
  };

  function previewCard(data) {
    const items = data.items || [];
    const meta = el("dl", { class: "kv" }, ...[["Title", data.title], ["Fetched with", data.fetched_with], ["Status", data.status_code], ["Author", data.author], ["Date", data.date], ["Items on page", data.items_total]]
      .filter(([, v]) => v != null && v !== "").flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]));
    const box = el("div");
    const show = (key) => {
      if (key === "items") box.replaceChildren(items.length ? recordTable(items) : el("p", { class: "hint", text: "No list of items on this page — the text tab has its content." }));
      else box.replaceChildren(el("div", { class: "md" }, ...markdown(data.markdown || "")));
    };
    const card = el("section", { class: "card preview" },
      el("h2", {}, icon("eye"), "Quick look", el("span", { class: "eyebrow", text: data.final_url || data.url })), el("div", { style: "margin:14px 0" }, meta),
      tabs([{ key: "items", label: "Items", note: items.length }, { key: "text", label: "Page text" }], items.length ? "items" : "text", show), box);
    show(items.length ? "items" : "text");
    return card;
  }

  function recordTable(rows) {
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    return el("div", { class: "tablewrap", style: "max-height:420px" }, el("table", { class: "data" },
      el("thead", {}, el("tr", {}, el("th", { class: "idx", text: "#" }), ...cols.map((c) => el("th", { text: c })))),
      el("tbody", {}, ...rows.map((r, i) => el("tr", { style: `animation-delay:${Math.min(i, 20) * 18}ms`, onclick: () => openDrawer(r, `Item ${i + 1}`) },
        el("td", { class: "idx", text: i + 1 }), ...cols.map((c) => el("td", {}, cell(r[c]))))))));
  }

  /** Tiny Markdown: headings, lists, paragraphs, links, bold, code. Built as DOM, never HTML. */
  function markdown(text) {
    const inline = (line) => {
      const out = [], re = /\[([^\]]+)\]\((https?:[^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g;
      let last = 0, m;
      while ((m = re.exec(line))) {
        if (m.index > last) out.push(line.slice(last, m.index));
        if (m[1]) out.push(el("a", { href: m[2], target: "_blank", rel: "noopener noreferrer", text: m[1] }));
        else if (m[3]) out.push(el("strong", { text: m[3] }));
        else out.push(el("code", { text: m[4] }));
        last = re.lastIndex;
      }
      out.push(line.slice(last));
      return out;
    };
    const nodes = []; let list = null;
    for (const raw of text.split("\n")) {
      const line = raw.trimEnd();
      const h = /^(#{1,3})\s+(.*)/.exec(line), li = /^\s*[-*+]\s+(.*)/.exec(line);
      if (li) { if (!list) { list = el("ul"); nodes.push(list); } list.append(el("li", {}, ...inline(li[1]))); continue; }
      list = null;
      if (h) nodes.push(el("h" + h[1].length, {}, ...inline(h[2])));
      else if (line.trim()) nodes.push(el("p", {}, ...inline(line)));
    }
    return nodes.length ? nodes : [el("p", { class: "hint", text: "No readable text found." })];
  }

  // ---------------------------------------------------------------- cells, drawer
  const isUrl = (v) => typeof v === "string" && /^https?:\/\/\S+$/.test(v);
  function highlight(text, needle) {
    if (!needle) return [text];
    const out = [], lower = text.toLowerCase(), n = needle.toLowerCase();
    let i = 0, j;
    while ((j = lower.indexOf(n, i)) !== -1 && out.length < 40) { out.push(text.slice(i, j), el("mark", { text: text.slice(j, j + n.length) })); i = j + n.length; }
    out.push(text.slice(i));
    return out;
  }
  function cell(value, needle) {
    if (value == null || value === "") return el("span", { class: "nil", text: "—" });
    if (typeof value === "object") return el("span", { class: "num", text: JSON.stringify(value).slice(0, 300) });
    if (isUrl(value)) {
      let label = value; try { const u = new URL(value); label = u.host + u.pathname; } catch (e) { /* keep raw */ }
      return el("a", { href: value, target: "_blank", rel: "noopener noreferrer", title: value, onclick: (e) => e.stopPropagation() }, ...highlight(label, needle));
    }
    const text = String(value);
    if (typeof value === "number" || /^-?[\d.,]+$/.test(text)) return el("span", { class: "num" }, ...highlight(text, needle));
    return el("span", { title: text.length > 60 ? text.slice(0, 500) : null }, ...highlight(text, needle));
  }

  function openDrawer(record, title) {
    const drawer = $("#drawer");
    const name = record.title || record.name || record.headline || title || "Record";
    $("#drawerTitle").textContent = String(name).slice(0, 120);
    const rows = Object.entries(record).map(([k, v], i) => {
      const text = typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : String(v ?? "");
      const body = el("div", { class: "v" });
      if (isUrl(text)) body.append(el("a", { href: text, target: "_blank", rel: "noopener noreferrer", style: "color:var(--tide)", text }));
      else body.textContent = text || "—";
      if (isUrl(text) && (/(image|img|thumb|photo|picture)/i.test(k) || /\.(png|jpe?g|webp|gif|avif)(\?|$)/i.test(text))) body.append(el("img", { src: text, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }));
      return el("div", { class: "fieldrow", style: `animation-delay:${Math.min(i, 16) * 30}ms` },
        el("div", { class: "k" }, el("span", { text: k }), el("button", { text: "copy", onclick: () => copy(text) })), body);
    });
    const actions = el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
      el("button", { class: "btn small", onclick: () => copy(JSON.stringify(record, null, 2)) }, icon("copy"), "Copy JSON"),
      record.url && isUrl(record.url) ? el("a", { class: "btn small", href: record.url, target: "_blank", rel: "noopener noreferrer" }, icon("link"), "Open page") : null);
    $("#drawerBody").replaceChildren(actions, ...rows);
    drawer.hidden = false; drawer.classList.remove("closing");
    $("#drawerClose").focus();
  }
  function closeDrawer() {
    const drawer = $("#drawer");
    if (drawer.hidden) return;
    drawer.classList.add("closing");
    setTimeout(() => { drawer.hidden = true; drawer.classList.remove("closing"); }, reduced ? 0 : 280);
  }
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });

  // ---------------------------------------------------------------- live
  VIEWS.live = async (root, parts, ctx) => {
    crumbs("Seine", "Live");
    root.append(el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "Crawls" }), el("h1", {}, "The net, ", el("em", { text: "live" })),
      el("p", { text: "Watch the spider walk each page it crawls: it drops in on a thread, wraps every item it takes and taps the links it will follow next. The radar on the left maps the site: one slice per section, one ring per level deeper." })),
      el("div", { class: "actions" }, el("a", { class: "btn primary", href: "#/extract" }, icon("bolt"), "New extraction"))));
    const listBox = el("div", { class: "jobs" });
    const mapSlot = el("div");
    const monitor = el("div", { class: "monitor" });
    root.append(el("div", { class: "live" }, el("div", { class: "live-left" }, listBox, mapSlot), monitor));

    let selected = parts[0] || null, timer = null, net = null, spider = null, since = 0, feedEl = null, gauges = null, lastStatus = null;

    function drawList() {
      const jobs = state.jobs;
      if (!jobs.length) {
        listBox.replaceChildren(el("div", { class: "card empty", style: "padding:30px 16px" }, el("p", { text: "No crawls in this session yet." })));
        return;
      }
      const first = !listBox.classList.contains("settled");
      listBox.classList.add("settled");
      listBox.replaceChildren(...jobs.map((j, i) => el("button", { class: "job" + (j.id === selected ? " on" : ""), style: first ? `animation-delay:${i * 40}ms` : "animation:none", onclick: () => { if (j.id !== selected) go(`#/live/${j.id}`); } },
        el("div", { class: "u", text: j.urls.map((u) => u.replace(/^https?:\/\//, "")).join(", ") }),
        el("div", { class: "m" }, el("span", { class: "state " + j.status, text: j.status }), el("span", { text: `${j.progress.pages}p · ${nf.format(j.progress.items)} items` })))));
    }

    function mountMonitor(job) {
      const ringVal = el("b", { text: "0" });
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      circle.setAttribute("viewBox", "0 0 160 160");
      circle.innerHTML = '<circle class="track" cx="80" cy="80" r="66"/><circle class="val" cx="80" cy="80" r="66" stroke-dasharray="414.7" stroke-dashoffset="414.7"/>';
      const gauge = (label) => { const num = el("div", { class: "num", text: "0" }); return [el("div", { class: "card gauge" }, el("div", { class: "eyebrow", text: label }), num), num]; };
      const [gItems, nItems] = gauge("Items caught"), [gQueue, nQueue] = gauge("Waiting in queue"), [gRef, nRef] = gauge("Refused / failed");
      const stopBtn = el("button", { class: "btn danger small", onclick: async () => { try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); toast("Stopping — saving what was caught"); } catch (err) { toast(err.message, true); } } }, icon("stop"), "Stop");
      const head = el("div", { class: "card", style: "display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 20px" },
        el("span", { class: "state " + job.status, text: job.status }), el("b", { style: "font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0", text: job.urls.join("  ·  ") }), stopBtn);
      const banner = el("div");
      const tip = el("div", { class: "tip" });
      const canvas = el("canvas");
      const radarStats = el("div", { class: "radar-stats" });
      const netCard = el("div", { class: "card net" }, canvas, tip,
        el("div", { class: "nettitle" }, el("div", { class: "eyebrow", text: "crawl radar" }), radarStats),
        el("div", { class: "legend" }, ...[["ok", "fetched"], ["refused", "refused"], ["error", "error"], ["pending", "seen"]].map(([k, l]) => el("span", {}, el("i", { style: `background:var(${{ ok: "--lime", refused: "--coral", error: "--amber", pending: "--ink-3" }[k]})` }), l))));
      feedEl = el("div", { class: "feed", role: "log" });
      const filter = { level: "all" };
      const feedCard = el("div", { class: "card" }, el("h2", {}, "Event feed", el("span", { style: "margin-left:auto" }, seg([["all", "All"], ["WARNING", "Warnings"]], "all", (v) => { filter.level = v; $$(".ev", feedEl).forEach((n) => { n.hidden = v !== "all" && n.classList.contains("INFO"); }); }))), feedEl);
      feedEl.filter = filter;
      const action = el("div", { class: "spider-action", text: "" });
      const carried = el("div", { class: "num", text: "0" });
      const spiderCanvas = el("canvas", { "aria-hidden": "true" });
      const sheets = el("div", { class: "sheets" });
      const addr = el("span", { class: "addr-url", text: "waiting…" }), chip = el("span", { class: "addr-chip" }), tabTitle = el("span", { class: "tab-title", text: "New tab" });
      const spiderCard = el("div", { class: "card walk", role: "img", "aria-label": "The page being crawled, with an animated spider walking it" },
        el("div", { class: "browser" }, el("div", { class: "dots" }, el("i"), el("i"), el("i")), el("div", { class: "tab" }, icon("page"), tabTitle),
          el("div", { class: "addr" }, chip, addr)),
        el("div", { class: "viewport" }, sheets, spiderCanvas),
        el("div", { class: "walk-foot" }, el("div", { style: "min-width:0" }, el("div", { class: "eyebrow", text: "the crawler" }), action),
          el("div", { class: "carried" }, el("div", { class: "eyebrow", text: "carried home" }), carried)));
      mapSlot.replaceChildren(netCard);
      monitor.replaceChildren(head, banner,
        el("div", { class: "gauges" }, el("div", { class: "card ring" }, circle, el("div", { class: "lbl" }, ringVal, el("span", { text: "pages" }))), gItems, gQueue, gRef),
        spiderCard, feedCard);
      net = new Radar(canvas, tip, radarStats);
      spider = new PageWalk({ canvas: spiderCanvas, sheets,
        tab: (text) => { tabTitle.textContent = text; },
        address: (url, status) => { addr.textContent = url; chip.className = "addr-chip " + status; chip.textContent = status === "ok" ? "200" : status === "refused" ? "refused" : status === "error" ? "error" : "…"; },
        action: (text) => { if (action.textContent !== text) { action.textContent = text; action.animate?.([{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }], { duration: 260, easing: "ease-out" }); } },
        carried: (n) => { carried.textContent = nf.format(n); carried.animate?.([{ transform: "scale(1.12)" }, { transform: "none" }], { duration: 220 }); },
      });
      gauges = { ringVal, circle: $(".val", circle), nItems, nQueue, nRef, stopBtn, head, banner };
    }

    function updateMonitor(job) {
      const p = job.progress, g = gauges;
      g.ringVal.textContent = nf.format(p.pages);
      const frac = Math.min(1, p.pages / Math.max(1, job.max_pages));
      g.circle.style.strokeDashoffset = String(414.7 * (1 - (job.status === "done" ? 1 : frac)));
      g.nItems.textContent = nf.format(p.items); g.nQueue.textContent = nf.format(p.queued);
      const bad = Object.entries(p.statuses).filter(([s]) => s !== "success" && s !== "duplicate_content").reduce((s, [, n]) => s + n, 0);
      g.nRef.textContent = nf.format(bad);
      const st = $(".state", g.head); st.className = "state " + job.status; st.textContent = job.status;
      g.stopBtn.hidden = !["running", "queued"].includes(job.status);
      if (lastStatus !== job.status && ["done", "cancelled", "failed"].includes(job.status)) {
        const ok = job.status !== "failed";
        g.banner.replaceChildren(el("div", { class: "done-banner" + (ok ? "" : " bad") },
          el("h3", { text: ok ? `${nf.format(p.items)} items in the net` : "The crawl failed" }),
          el("span", { class: "hint", text: ok ? `${p.pages} pages in ${Math.round((job.finished - job.started) || 0)}s${job.status === "cancelled" ? " · stopped early" : ""}` : job.error || "" }),
          job.run_id ? el("div", { class: "actions" }, el("a", { class: "btn primary small", href: runHash(job.run_id) }, icon("eye"), "Open results")) : null));
        state.runs = null;  // the library has a new run
        spider.finish();
      }
      lastStatus = job.status;
    }

    function addEvents(events) {
      const frag = document.createDocumentFragment();
      for (const ev of events) {
        if (ev.message === "page_sketch") { spider.sketch(ev.url, ev.sketch); continue; }
        if (ev.url) {
          const status = pageStatus(ev);
          if (ev.message !== "trying_browser" || !net.nodes.has(net.key(ev.url))) net.add(ev.url, status);
          if (ev.message === "http_request" || status === "refused" || status === "error") spider.visit(ev.url, status);
          if (ev.message === "parsing_complete") spider.collect(ev.url, ev.items_extracted || 0);
        }
        const detail = [];
        if (ev.url) { try { const u = new URL(ev.url); detail.push(u.pathname + u.search); } catch (e) { detail.push(ev.url); } }
        for (const k of ["status", "items_extracted", "total_items", "pages_visited", "duration_ms", "reason", "error", "seconds"]) if (ev[k] != null) detail.push(`${k.replace(/_/g, " ")} ${ev[k]}`);
        const t = ev.timestamp ? new Date(ev.timestamp).getTime() / 1000 : Date.now() / 1000;
        const row = el("div", { class: "ev " + (ev.level || "INFO") }, el("time", { text: clock(t) }), el("b", { text: String(ev.message || "").replace(/_/g, " ") }), el("span", { text: detail.join(" · "), title: detail.join(" · ") }));
        if (feedEl.filter.level !== "all" && (ev.level || "INFO") === "INFO") row.hidden = true;
        frag.append(row);
      }
      const atBottom = feedEl.scrollHeight - feedEl.scrollTop - feedEl.clientHeight < 40;
      feedEl.append(frag);
      while (feedEl.children.length > 500) feedEl.firstChild.remove();
      if (atBottom) feedEl.scrollTop = feedEl.scrollHeight;
    }

    async function tick() {
      try {
        if (!selected) return;
        const job = await api(`/api/jobs/${selected}`, { query: { since } });
        if (!ctx.alive()) return;
        if (!gauges) mountMonitor(job);
        since = job.last_event;
        addEvents(job.events || []);
        updateMonitor(job);
        const i = state.jobs.findIndex((j) => j.id === job.id);
        if (i >= 0) { state.jobs[i] = job; drawList(); }
        if (["done", "cancelled", "failed"].includes(job.status)) return;
      } catch (err) {
        if (!ctx.alive()) return;
        monitor.replaceChildren(el("div", { class: "card empty" }, el("h3", { text: "That crawl is gone" }), el("p", { text: "Jobs live as long as the studio runs. Its results are still in the Library." })));
        return;
      }
      timer = setTimeout(tick, 700);
    }

    await pollJobs();
    if (!ctx.alive()) return;
    if (!selected && state.jobs.length) selected = (state.jobs.find((j) => j.status === "running") || state.jobs[0]).id;
    drawList();
    if (selected) tick();
    else monitor.replaceChildren(el("div", { class: "card empty" }, icon("radar"), el("h3", { text: "Calm waters" }), el("p", { text: "Start an extraction and it shows up here as it runs." }),
      el("a", { class: "btn primary", href: "#/extract", style: "margin-top:12px" }, icon("bolt"), "New extraction")));
    const listTimer = setInterval(drawList, 2500);
    return () => { clearTimeout(timer); clearInterval(listTimer); net?.destroy(); spider?.destroy(); };
  };

  // ---------------------------------------------------------------- library
  VIEWS.library = async (root, _parts, ctx) => {
    crumbs("Seine", "Library");
    const prefs = Object.assign({ sort: "new", layout: "grid" }, store.get("library", {}));
    const q = el("input", { class: "input", placeholder: "Search runs, sites, files…  ( / )", id: "librarySearch", "aria-label": "Search runs" });
    const siteSel = el("select", { class: "input", "aria-label": "Filter by site" }, el("option", { value: "", text: "All sites" }));
    const sortSel = el("select", { class: "input", "aria-label": "Sort runs" }, ...[["new", "Newest first"], ["old", "Oldest first"], ["items", "Most items"], ["size", "Largest"], ["site", "Site A–Z"]].map(([v, t]) => el("option", { value: v, text: t, selected: v === prefs.sort })));
    const grid = el("div", { class: "runs" + (prefs.layout === "list" ? " list" : "") });
    const countNote = el("span", { class: "hint" });
    root.append(
      el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "Everything you've caught" }), el("h1", {}, "The ", el("em", { text: "library" })),
        el("p", { text: "Every run is a folder in outputs/results. Open one to search, sort and inspect its records." })),
        el("div", { class: "actions" }, el("button", { class: "btn", onclick: () => api("/api/reveal", { method: "POST" }).catch((e) => toast(e.message, true)) }, icon("folder"), "Open folder"),
          el("button", { class: "btn", onclick: async () => { await loadRuns(true); draw(); toast("Library refreshed"); } }, "Refresh"))),
      el("div", { class: "toolbar" }, el("div", { class: "search" }, icon("search"), q), siteSel, sortSel,
        seg([["grid", "Grid"], ["list", "List"]], prefs.layout, (v) => { prefs.layout = v; store.set("library", prefs); grid.classList.toggle("list", v === "list"); $$(".run", grid).forEach((c) => (c.style.transform = "")); }), countNote),
      grid);
    grid.replaceChildren(...Array.from({ length: 6 }, () => el("div", { class: "skeleton", style: "height:170px" })));

    const runs = await loadRuns(true);
    if (!ctx.alive()) return;
    for (const site of [...new Set(runs.map((r) => r.site))].sort()) siteSel.append(el("option", { value: site, text: site }));
    if (ctx.params.get("site")) siteSel.value = ctx.params.get("site");

    function draw() {
      const needle = q.value.trim().toLowerCase();
      let list = (state.runs || []).filter((r) => (!siteSel.value || r.site === siteSel.value) &&
        (!needle || (r.name + " " + r.site + " " + r.files.map((f) => f.name).join(" ")).toLowerCase().includes(needle)));
      const by = { new: (a, b) => b.created - a.created, old: (a, b) => a.created - b.created, items: (a, b) => (b.items || 0) - (a.items || 0), size: (a, b) => b.size - a.size, site: (a, b) => a.site.localeCompare(b.site) }[sortSel.value];
      list = list.sort(by);
      countNote.textContent = `${list.length} run${list.length === 1 ? "" : "s"} · ${nf.format(list.reduce((s, r) => s + (r.items || 0), 0))} items`;
      grid.replaceChildren(...(list.length ? list.map((r, i) => runCard(r, i)) : [el("div", { class: "empty", style: "grid-column:1/-1" }, icon("search"), el("h3", { text: "Nothing matches" }), el("p", { text: "Try another word, or clear the site filter." }))]));
    }
    q.addEventListener("input", debounce(draw, 120));
    siteSel.addEventListener("change", draw);
    sortSel.addEventListener("change", () => { prefs.sort = sortSel.value; store.set("library", prefs); draw(); });
    draw();
  };

  // ---------------------------------------------------------------- run viewer
  VIEWS.run = async (root, parts, ctx) => {
    const id = parts[0] || "";
    const runs = await loadRuns(true);
    if (!ctx.alive()) return;
    const run = runs.find((r) => r.id === id);
    if (!run) { crumbs("Seine", "Library", "missing"); root.append(el("div", { class: "empty" }, el("h3", { text: "This run is gone" }), el("p", { text: id }), el("a", { class: "btn", href: "#/library" }, "Back to the library"))); return; }
    crumbs("Seine", "Library", run.name);
    const order = ["jsonl", "csv", "json", "sqlite", "db"];
    const files = [...run.files].sort((a, b) => (order.indexOf(a.format) + 1 || 99) - (order.indexOf(b.format) + 1 || 99));
    let file = files.find((f) => f.path === ctx.params.get("file")) || files[0];
    const opts = { q: "", sort: null, desc: false, mode: store.get("viewMode", "table"), hidden: {} };
    let rows = [], columns = [], matched = 0, total = 0, loading = false, selectedRow = -1;

    const dl = el("a", { class: "btn", href: "#" }, icon("download"), "Download");
    const header = el("div", { class: "page-head" },
      el("div", { style: "display:flex;gap:16px;align-items:center" }, mark(run.site),
        el("div", {}, el("div", { class: "eyebrow", text: `${new Date(run.created * 1000).toLocaleString()} · ${fmtBytes(run.size)}` }), el("h1", { style: "font-size:46px", text: run.site }))),
      el("div", { class: "actions" }, dl,
        el("button", { class: "btn", onclick: () => api("/api/reveal", { method: "POST", query: { path: run.id } }).catch((e) => toast(e.message, true)) }, icon("folder"), "Folder"),
        el("button", { class: "btn danger", onclick: async () => { if (await deleteRun(run)) go("#/library"); } }, icon("trash"), "Delete")));
    const search = el("input", { class: "input", placeholder: "Search inside this file…  ( / )", id: "librarySearch", "aria-label": "Search records" });
    const colBtn = el("button", { class: "btn small" }, icon("columns"), "Columns");
    const colMenu = el("div", { class: "menu" }, colBtn);
    const modeSeg = seg([["table", "Table"], ["cards", "Cards"], ["json", "JSON"]], opts.mode, (v) => { opts.mode = v; store.set("viewMode", v); paint(); });
    const toolbar = el("div", { class: "toolbar" }, el("div", { class: "search" }, icon("search"), search), modeSeg, colMenu);
    const body = el("div");
    const pager = el("div", { class: "pager" });
    const fileTabs = tabs(files.map((f) => ({ key: f.path, label: f.name.length > 46 ? "…" + f.name.slice(-44) : f.name, note: f.rows != null ? nf.format(f.rows) : fmtBytes(f.size) })), file.path,
      (key) => { file = files.find((f) => f.path === key); history.replaceState(null, "", runHash(run.id, key)); opts.sort = null; opts.q = ""; search.value = ""; opts.hidden = {}; load(true); });
    root.append(header, fileTabs, toolbar, body, pager);

    colBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = $(".menu-pop", colMenu); if (open) { open.remove(); return; }
      const pop = el("div", { class: "menu-pop", onclick: (ev) => ev.stopPropagation() }, ...columns.map((c) => el("label", {},
        el("input", { type: "checkbox", checked: !opts.hidden[c], onchange: (ev) => { opts.hidden[c] = !ev.target.checked; paint(); } }), c)));
      colMenu.append(pop);
      const away = () => { pop.remove(); document.removeEventListener("click", away); };
      setTimeout(() => document.addEventListener("click", away), 0);
    });

    async function load(reset) {
      if (loading) return;
      loading = true;
      if (reset) { rows = []; selectedRow = -1; body.replaceChildren(el("div", { class: "skeleton", style: "height:360px" })); }
      dl.href = "/api/download?path=" + encodeURIComponent(file.path);
      try {
        const data = await api("/api/file", { query: { path: file.path, offset: rows.length, limit: 150, q: opts.q, sort: opts.sort, desc: opts.desc ? 1 : "" } });
        if (!ctx.alive()) return;
        if (data.kind === "text") {
          toolbar.hidden = true; pager.replaceChildren();
          body.replaceChildren(el("pre", { class: "code", text: data.text + (data.truncated ? "\n\n… (first 1 MB shown — download for the rest)" : "") }));
          return;
        }
        toolbar.hidden = false;
        columns = data.columns; matched = data.matched; total = data.total;
        rows = rows.concat(data.rows);
        paint(reset ? 0 : rows.length - data.rows.length);
      } catch (err) { body.replaceChildren(el("div", { class: "done-banner bad" }, el("h3", { text: "Couldn't read this file" }), el("span", { text: err.message }))); }
      finally { loading = false; }
    }

    const visible = () => columns.filter((c) => !opts.hidden[c]);
    function paint(from = 0) {
      const cols = visible(), needle = opts.q;
      if (!rows.length) {
        body.replaceChildren(el("div", { class: "empty card" }, icon("search"), el("h3", { text: total ? "No matches" : "Empty file" }), el("p", { text: total ? "Nothing in this file contains that text." : "This run caught nothing." })));
      } else if (opts.mode === "table") {
        let tbody = $("tbody", body);
        if (!tbody || from === 0) {
          const head = el("tr", {}, el("th", { class: "idx", text: "#" }), ...cols.map((c) => el("th", { class: opts.sort === c && opts.desc ? "desc" : "", title: "Sort by " + c,
            onclick: () => { if (opts.sort === c) { if (opts.desc) { opts.sort = null; opts.desc = false; } else opts.desc = true; } else { opts.sort = c; opts.desc = false; } load(true); } },
            c, opts.sort === c ? el("span", { class: "arr", text: "↑" }) : null)));
          tbody = el("tbody");
          body.replaceChildren(el("div", { class: "tablewrap", id: "tablewrap" }, el("table", { class: "data" }, el("thead", {}, head), tbody)));
          from = 0;
        }
        const frag = document.createDocumentFragment();
        rows.slice(from).forEach((r, k) => {
          const i = from + k;
          frag.append(el("tr", { "data-i": i, style: `animation-delay:${Math.min(k, 24) * 14}ms`, onclick: () => { select(i); openDrawer(r, `Record ${i + 1}`); } },
            el("td", { class: "idx", text: i + 1 }), ...cols.map((c) => el("td", {}, cell(r[c], needle)))));
        });
        tbody.append(frag);
        watchEnd($("#tablewrap", body), $("tbody", body).lastElementChild);
      } else if (opts.mode === "cards") {
        const titleKey = ["title", "name", "headline", "text", "value"].find((k) => cols.includes(k)) || cols[0];
        let grid = $(".cards", body);
        if (!grid || from === 0) { grid = el("div", { class: "cards" }); body.replaceChildren(grid); from = 0; }
        rows.slice(from).forEach((r, k) => {
          const i = from + k;
          const rest = cols.filter((c) => c !== titleKey && r[c] != null && r[c] !== "").slice(0, 6);
          grid.append(el("article", { class: "rec", style: `animation-delay:${Math.min(k, 24) * 20}ms`, onclick: () => openDrawer(r, `Record ${i + 1}`) },
            el("h4", {}, ...highlight(String(r[titleKey] ?? `Record ${i + 1}`), needle)),
            el("dl", {}, ...rest.flatMap((c) => [el("dt", { text: c }), el("dd", {}, cell(r[c], needle))]))));
        });
        watchEnd(null, grid.lastElementChild);
      } else {
        body.replaceChildren(jsonView(rows.map((r) => Object.fromEntries(cols.map((c) => [c, r[c]]))) ));
      }
      const shown = rows.length;
      pager.replaceChildren(el("span", { text: `${nf.format(shown)} of ${nf.format(matched)}${matched !== total ? ` matching (${nf.format(total)} total)` : ""}` }),
        el("div", { class: "bar" }, el("i", { style: `width:${matched ? (shown / matched) * 100 : 0}%` })),
        shown < matched ? el("button", { class: "btn small", onclick: () => load(false) }, "Load more") : el("span", { text: "all loaded" }));
    }

    let io = null;
    function watchEnd(scrollRoot, last) {
      io?.disconnect();
      if (!last || rows.length >= matched) return;
      io = new IntersectionObserver((entries) => { if (entries[0].isIntersecting) { io.disconnect(); load(false); } }, { root: scrollRoot, rootMargin: "300px" });
      io.observe(last);
    }

    function select(i) {
      selectedRow = i;
      $$("tbody tr", body).forEach((tr) => tr.classList.toggle("sel", +tr.dataset.i === i));
      $(`tbody tr[data-i="${i}"]`, body)?.scrollIntoView({ block: "nearest" });
    }

    search.addEventListener("input", debounce(() => { opts.q = search.value.trim(); load(true); }, 250));
    const onKey = (e) => {
      if (e.target.matches("input, textarea, select") || opts.mode !== "table" || !rows.length || !$("#drawer").hidden) return;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); select(Math.min(rows.length - 1, selectedRow + 1)); }
      if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); select(Math.max(0, selectedRow - 1)); }
      if (e.key === "Enter" && selectedRow >= 0) openDrawer(rows[selectedRow], `Record ${selectedRow + 1}`);
    };
    document.addEventListener("keydown", onKey);
    load(true);
    return () => { document.removeEventListener("keydown", onKey); io?.disconnect(); };
  };

  function jsonView(data) {
    const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    const text = esc(JSON.stringify(data, null, 2) || "");
    const pre = el("pre", { class: "code" });
    // input is escaped above, so only these spans are markup
    pre.innerHTML = text.replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi,
      (m, str, colon, bool, num) => str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`) : bool ? `<span class="b">${bool}</span>` : `<span class="n">${num}</span>`);
    return pre;
  }

  // ---------------------------------------------------------------- jobs poller
  async function pollJobs() {
    try {
      const { jobs } = await api("/api/jobs");
      state.jobs = jobs;
      const active = jobs.filter((j) => ["running", "queued"].includes(j.status)).length;
      const badge = $("#liveBadge"); badge.hidden = !active; badge.textContent = active;
      $("#livePill").hidden = !active;
      for (const j of jobs) {
        const before = state.seenStatus[j.id];
        if (before && before !== j.status && ["done", "failed", "cancelled"].includes(j.status)) {
          state.runs = null;
          if (j.status === "failed") toast(`Crawl failed: ${j.error || j.urls[0]}`, true);
          else toast(`${j.status === "done" ? "Haul complete" : "Stopped"}: ${nf.format(j.progress.items)} items from ${j.urls[0].replace(/^https?:\/\//, "")}`, false, j.run_id ? () => go(runHash(j.run_id)) : null);
        }
        state.seenStatus[j.id] = j.status;
      }
    } catch (e) { /* studio restarting; try again next tick */ }
  }
  setInterval(pollJobs, 2500);

  // ---------------------------------------------------------------- command palette
  const palette = $("#palette"), pInput = $("#paletteInput"), pList = $("#paletteList");
  let pItems = [], pIndex = 0;
  function toggleTheme() {
    const now = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    // stored raw, not as JSON: the inline script in index.html reads it before this file loads
    const apply = () => { document.documentElement.dataset.theme = now; try { localStorage.setItem("seine.theme", now); } catch (e) { /* private window */ } };
    if (document.startViewTransition && !reduced) document.startViewTransition(apply); else apply();
  }

  async function openPalette() {
    palette.hidden = false; pInput.value = ""; pInput.focus();
    const base = [
      { g: "Go", t: "Overview", i: "home", run: () => go("#/") },
      { g: "Go", t: "New extraction", i: "bolt", k: "N", run: () => go("#/extract") },
      { g: "Go", t: "Live crawls", i: "radar", run: () => go("#/live") },
      { g: "Go", t: "Library", i: "books", run: () => go("#/library") },
      { g: "Do", t: "Switch light / dark", i: "moon", k: "T", run: toggleTheme },
      { g: "Do", t: "Open the results folder", i: "folder", run: () => api("/api/reveal", { method: "POST" }).catch((e) => toast(e.message, true)) },
    ];
    pItems = base; drawPalette();
    try { const runs = await loadRuns(); pItems = base.concat(runs.map((r) => ({ g: "Run", t: `${r.site} — ${r.items ?? "?"} items`, s: ago(r.created), i: "page", hay: r.name, run: () => go(runHash(r.id)) }))); drawPalette(); } catch (e) { /* palette still works */ }
  }
  function fuzzy(hay, needle) { let i = 0; hay = hay.toLowerCase(); for (const c of needle.toLowerCase()) { i = hay.indexOf(c, i); if (i < 0) return false; i++; } return true; }
  function drawPalette() {
    const n = pInput.value.trim();
    const shown = pItems.filter((it) => !n || fuzzy(it.t + " " + (it.hay || ""), n)).slice(0, 40);
    pIndex = Math.min(pIndex, Math.max(0, shown.length - 1));
    pList.replaceChildren(...shown.map((it, i) => el("li", { class: i === pIndex ? "on" : "", role: "option", onmouseenter: () => { pIndex = i; $$("li", pList).forEach((li, k) => li.classList.toggle("on", k === i)); }, onclick: () => { closePalette(); it.run(); } },
      icon(it.i), el("span", { class: "grp", text: it.g }), el("span", { text: it.t }), it.s || it.k ? el("small", { text: it.s || it.k }) : null)));
    pList.shown = shown;
  }
  function closePalette() { palette.hidden = true; }
  pInput.addEventListener("input", () => { pIndex = 0; drawPalette(); });
  pInput.addEventListener("keydown", (e) => {
    const shown = pList.shown || [];
    if (e.key === "ArrowDown") { e.preventDefault(); pIndex = (pIndex + 1) % Math.max(1, shown.length); drawPalette(); $("li.on", pList)?.scrollIntoView({ block: "nearest" }); }
    if (e.key === "ArrowUp") { e.preventDefault(); pIndex = (pIndex - 1 + shown.length) % Math.max(1, shown.length); drawPalette(); $("li.on", pList)?.scrollIntoView({ block: "nearest" }); }
    if (e.key === "Enter" && shown[pIndex]) { closePalette(); shown[pIndex].run(); }
  });
  palette.addEventListener("click", (e) => { if (e.target === palette) closePalette(); });
  $("#paletteBtn").addEventListener("click", openPalette);
  $("#searchTrigger").addEventListener("click", openPalette);
  $("#themeBtn").addEventListener("click", toggleTheme);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette.hidden ? openPalette() : closePalette(); return; }
    if (e.key === "Escape") { if (!palette.hidden) closePalette(); else closeDrawer(); return; }
    if (e.target.matches("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey || !palette.hidden) return;
    if (e.key === "/") { const s = $("#librarySearch"); if (s) { e.preventDefault(); s.focus(); } }
    else if (e.key === "n" || e.key === "N") go("#/extract");
    else if (e.key === "t" || e.key === "T") toggleTheme();
    else if (["1", "2", "3", "4"].includes(e.key)) go(["#/", "#/extract", "#/live", "#/library"][+e.key - 1]);
  });

  window.addEventListener("hashchange", route);
  window.addEventListener("resize", debounce(() => setNav($("#nav a.active")?.dataset.view), 100));
  pollJobs();
  route();
})();
