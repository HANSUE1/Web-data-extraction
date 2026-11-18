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
      tokenCache = { theme, line: v("--line-2"), lime: v("--lime"), coral: v("--coral"), amber: v("--amber"), ink: v("--ink"), ink3: v("--ink-3"), bg: v("--panel") };
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

  // ---------------------------------------------------------------- crawl constellation (live)
  class Constellation {
    constructor(canvas, tip) {
      this.canvas = canvas; this.tip = tip; this.ctx = canvas.getContext("2d");
      this.nodes = new Map(); this.list = []; this.view = { x: 0, y: 0, k: 1 }; this.hover = null;
      this.ro = new ResizeObserver(() => this.size()); this.ro.observe(canvas); this.size();
      this.bind(); this.raf = requestAnimationFrame((t) => this.frame(t));
    }
    size() { const r = this.canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1; this.w = r.width; this.h = r.height; this.canvas.width = r.width * dpr; this.canvas.height = r.height * dpr; this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
    key(url) { try { const u = new URL(url); return u.host + u.pathname.replace(/\/+$/, "") + (u.search || ""); } catch (e) { return url; } }
    parentOf(url) {
      let u; try { u = new URL(url); } catch (e) { return null; }
      const segs = u.pathname.split("/").filter(Boolean);
      if (u.search) { const k = u.host + u.pathname.replace(/\/+$/, ""); if (this.nodes.has(k)) return this.nodes.get(k); }
      for (let i = segs.length - 1; i >= 1; i--) { const k = u.host + "/" + segs.slice(0, i).join("/"); if (this.nodes.has(k)) return this.nodes.get(k); }
      return this.root(u.host);
    }
    root(host) {
      if (this.nodes.has(host)) return this.nodes.get(host);
      const n = this.make(host, "https://" + host + "/", null, "root"); n.r = 9; return n;
    }
    make(key, url, parent, status) {
      const a = Math.random() * Math.PI * 2;
      const base = parent || { x: this.w / 2, y: this.h / 2 };
      const n = { key, url, parent, status, x: base.x + Math.cos(a) * 30, y: base.y + Math.sin(a) * 30, vx: 0, vy: 0, r: 4.5, born: performance.now(), hits: 1 };
      this.nodes.set(key, n); this.list.push(n); return n;
    }
    add(url, status) {
      const key = this.key(url);
      let n = this.nodes.get(key);
      if (n) { if (n.status !== "root") n.status = status; n.hits++; n.born = performance.now(); return; }
      if (this.list.length > 600) return;  // enough to see the shape of a big crawl
      const parent = this.parentOf(url);
      if (parent && parent.key === key) { parent.status = status === "ok" ? "root" : status; return; }
      this.make(key, url, parent, status);
    }
    step() {
      const L = this.list, n = L.length, cx = this.w / 2, cy = this.h / 2;
      for (let i = 0; i < n; i++) {
        const a = L[i];
        for (let j = i + 1; j < n; j++) {
          const b = L[j]; let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || .01;
          if (d2 > 40000) continue;
          const f = 260 / d2; dx *= f; dy *= f; a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
        }
        if (a.parent) { const dx = a.parent.x - a.x, dy = a.parent.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - 46) * .02; a.vx += dx / d * f; a.vy += dy / d * f; a.parent.vx -= dx / d * f * .3; a.parent.vy -= dy / d * f * .3; }
        a.vx += (cx - a.x) * .0016; a.vy += (cy - a.y) * .0016;
      }
      for (const a of L) { if (a === this.drag) continue; a.vx *= .78; a.vy *= .78; a.x += Math.max(-12, Math.min(12, a.vx)); a.y += Math.max(-12, Math.min(12, a.vy)); }
    }
    frame(now) {
      this.step();
      const t = tokens();
      this.colors = { ok: t.lime, refused: t.coral, error: t.amber, root: t.ink, line: t.line, pending: t.ink3 };
      const { ctx } = this, v = this.view;
      ctx.clearRect(0, 0, this.w, this.h);
      ctx.save(); ctx.translate(v.x, v.y); ctx.scale(v.k, v.k);
      ctx.lineWidth = 1 / v.k; ctx.strokeStyle = this.colors.line; ctx.globalAlpha = .7;
      ctx.beginPath();
      for (const a of this.list) if (a.parent) { ctx.moveTo(a.x, a.y); ctx.lineTo(a.parent.x, a.parent.y); }
      ctx.stroke(); ctx.globalAlpha = 1;
      for (const a of this.list) {
        const age = Math.max(0, (now - a.born) / 900), color = this.colors[a.status] || this.colors.pending;
        if (age < 1) { ctx.globalAlpha = (1 - age) * .6; ctx.strokeStyle = color; ctx.lineWidth = 2 / v.k; ctx.beginPath(); ctx.arc(a.x, a.y, a.r + age * 22, 0, 6.283); ctx.stroke(); }
        const grow = Math.max(0, Math.min(1, (now - a.born) / 400 + (a.hits > 1 ? 1 : 0)));
        ctx.globalAlpha = 1; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(a.x, a.y, a.r * (.4 + .6 * (1 - Math.pow(1 - grow, 3))) * (a === this.hover ? 1.7 : 1), 0, 6.283); ctx.fill();
        if (a.status === "root") { ctx.font = `${12 / v.k}px "Geist Mono", monospace`; ctx.fillStyle = this.colors.root; ctx.globalAlpha = .8; ctx.fillText(a.key, a.x + 14, a.y + 4); ctx.globalAlpha = 1; }
      }
      ctx.restore();
      this.raf = requestAnimationFrame((t) => this.frame(t));
    }
    at(px, py) {
      const v = this.view, x = (px - v.x) / v.k, y = (py - v.y) / v.k;
      let best = null, bd = 140 / (v.k * v.k);
      for (const a of this.list) { const d = (a.x - x) ** 2 + (a.y - y) ** 2; if (d < bd) { bd = d; best = a; } }
      return best;
    }
    bind() {
      const c = this.canvas; let pan = null;
      const pos = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      c.addEventListener("pointerdown", (e) => { const [x, y] = pos(e); const hit = this.at(x, y); c.setPointerCapture(e.pointerId); if (hit) this.drag = hit; else pan = { x, y, vx: this.view.x, vy: this.view.y }; });
      c.addEventListener("pointermove", (e) => {
        const [x, y] = pos(e);
        if (this.drag) { this.drag.x = (x - this.view.x) / this.view.k; this.drag.y = (y - this.view.y) / this.view.k; return; }
        if (pan) { this.view.x = pan.vx + x - pan.x; this.view.y = pan.vy + y - pan.y; return; }
        this.hover = this.at(x, y);
        if (this.hover) { this.tip.replaceChildren(el("b", { text: this.hover.status + " " }), this.hover.url); this.tip.style.left = x + "px"; this.tip.style.top = y + "px"; this.tip.classList.add("on"); }
        else this.tip.classList.remove("on");
      });
      c.addEventListener("pointerup", () => { this.drag = null; pan = null; });
      c.addEventListener("dblclick", (e) => { const [x, y] = pos(e); const hit = this.at(x, y); if (hit && hit.url) window.open(hit.url, "_blank", "noopener"); });
      c.addEventListener("wheel", (e) => {
        e.preventDefault(); const [x, y] = pos(e), k = Math.max(.3, Math.min(3, this.view.k * (e.deltaY < 0 ? 1.12 : .89)));
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
   * A spider on a web. Each fetched page lands in the web as a fly; the spider walks
   * there on eight legs (two alternating groups of four, two-bone IK per leg), wraps it,
   * and the items found stream into its abdomen. Refused pages make it back off.
   */
  class SpiderScene {
    constructor(canvas, hud) {
      this.c = canvas; this.ctx = canvas.getContext("2d"); this.hud = hud;
      this.N = 16; this.spots = new Map(); this.order = []; this.tasks = []; this.particles = []; this.floats = [];
      this.carried = 0; this.finished = false; this.state = "idle"; this.task = null; this.workT = 0; this.restT = 0;
      this.s = { x: 0, y: 0, vx: 0, vy: 0, h: -Math.PI / 2, glow: 0, legs: [] };
      this.ro = new ResizeObserver(() => this.layout()); this.ro.observe(canvas);
      this.layout();
      this.s.x = this.cx; this.s.y = this.cy;
      this.initLegs();
      this.say("Waiting on the hub for the first page…");
      this.last = performance.now();
      this.raf = requestAnimationFrame((t) => this.frame(t));
    }

    // --- geometry
    layout() {
      const r = this.c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
      this.w = r.width; this.h = r.height; this.c.width = r.width * dpr; this.c.height = r.height * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.cx = this.w / 2; this.cy = this.h / 2 + 14;
      this.R = Math.max(80, Math.min(this.w * .62, this.h) / 2 - 18);
      this.K = Math.max(3, Math.floor((this.R - 34) / 30));
      this.angles = Array.from({ length: this.N }, (_, j) => (j / this.N) * Math.PI * 2 + Math.sin(j * 7.3) * .07);
    }
    ringR(k) { return 34 + (k / this.K) * (this.R - 34); }
    at(k, j) {
      const a = this.angles[((j % this.N) + this.N) % this.N], stretch = this.w > this.h ? 1.35 : 1;  // an oval web fills a wide card
      return { x: this.cx + Math.cos(a) * this.ringR(k) * stretch, y: this.cy + Math.sin(a) * this.ringR(k) };
    }
    pos(spot) { return this.at(spot.k, spot.j); }

    spotFor(url) {
      if (this.spots.has(url)) return this.spots.get(url);
      const taken = new Set([...this.spots.values()].map((s) => s.k + ":" + s.j));
      const j0 = hue(url) % this.N;
      for (let k = 1; k <= this.K; k++) for (let d = 0; d <= this.N / 2; d++) for (const j of [j0 + d, j0 - d]) {
        const jj = ((j % this.N) + this.N) % this.N;
        if (!taken.has(k + ":" + jj)) return this.place(url, k, jj);
      }
      const old = this.order.shift();  // the web is full: the oldest fly makes room
      this.spots.delete(old.url);
      return this.place(url, old.k, old.j);
    }
    place(url, k, j) {
      const spot = { url, k, j, status: "pending", born: performance.now(), items: 0, done: 0, wing: Math.random() * 6 };
      this.spots.set(url, spot); this.order.push(spot);
      return spot;
    }

    // --- events in
    visit(url, status) {
      const spot = this.spotFor(url);
      if (spot.status !== "pending" || this.tasks.some((t) => t.spot === spot) || this.task?.spot === spot) {
        if (status !== "pending" && spot.status !== "pending") spot.status = status;
        return;
      }
      this.tasks.push({ spot, status, items: 0 });
    }
    collect(url, items) {
      if (!items) return;
      const spot = this.spots.get(url);
      const task = [this.task, ...this.tasks].find((t) => t && t.spot === spot);
      if (task && !task.finalized) task.items += items;
      else if (spot) this.spray(spot, items);
      else this.carried += items, this.hud.carried(this.carried);
    }
    finish() { this.finished = true; }
    say(text) { this.hud.action(text); }

    // --- legs
    initLegs() {
      const S = this.scale = Math.max(.85, Math.min(1.25, this.R / 190));
      const rest = [[31, 24], [12, 34], [-8, 34], [-27, 27]];  // legs 1-2 reach forward, 3-4 back
      this.s.legs = [];
      for (const side of [-1, 1]) rest.forEach(([f, l], i) => {
        const leg = { side, i, f: f * S, l: l * side * S, hipF: [5, 2.5, 0, -2.5][i] * S, hipL: 3 * side * S,
          l1: 21 * S, l2: 26 * S, group: (i + (side > 0 ? 1 : 0)) % 2, stepping: false, t: 0 };
        const p = this.local(leg.f, leg.l); leg.x = p.x; leg.y = p.y;
        this.s.legs.push(leg);
      });
    }
    local(f, l, base = this.s) {
      const c = Math.cos(base.h), s = Math.sin(base.h);
      return { x: base.x + c * f - s * l, y: base.y + s * f + c * l };
    }
    updateLegs(dt) {
      const sp = this.s, speed = Math.hypot(sp.vx, sp.vy), lead = Math.min(.14, 6 / (speed + 1)) * speed;
      const dirx = speed ? sp.vx / speed : 0, diry = speed ? sp.vy / speed : 0;
      const busy = [0, 0];
      for (const leg of sp.legs) if (leg.stepping) busy[leg.group]++;
      for (const leg of sp.legs) {
        const ideal = this.local(leg.f, leg.l);
        if (leg.stepping) {
          leg.t = Math.min(1, leg.t + dt / leg.dur);
          const e = leg.t < .5 ? 2 * leg.t * leg.t : 1 - Math.pow(-2 * leg.t + 2, 2) / 2;
          leg.x = leg.fx + (leg.tx - leg.fx) * e; leg.y = leg.fy + (leg.ty - leg.fy) * e;
          if (leg.t >= 1) leg.stepping = false;
          continue;
        }
        const far = Math.hypot(ideal.x - leg.x, ideal.y - leg.y);
        if (far > 13 * this.scale && !busy[1 - leg.group]) {
          leg.stepping = true; leg.t = 0; leg.dur = Math.max(.07, .13 - speed / 4000);
          leg.fx = leg.x; leg.fy = leg.y; leg.tx = ideal.x + dirx * lead; leg.ty = ideal.y + diry * lead;
          busy[leg.group]++;
        }
      }
    }
    knee(hx, hy, fx, fy, l1, l2) {
      let dx = fx - hx, dy = fy - hy, d = Math.hypot(dx, dy) || .001;
      if (d > l1 + l2 - .5) { const k = (l1 + l2 - .5) / d; fx = hx + dx * k; fy = hy + dy * k; dx = fx - hx; dy = fy - hy; d = l1 + l2 - .5; }
      const a = Math.atan2(dy, dx), b = Math.acos(Math.max(-1, Math.min(1, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))));
      const k1 = { x: hx + Math.cos(a + b) * l1, y: hy + Math.sin(a + b) * l1 }, k2 = { x: hx + Math.cos(a - b) * l1, y: hy + Math.sin(a - b) * l1 };
      const away = (k) => Math.hypot(k.x - this.s.x, k.y - this.s.y);  // knees arch away from the body
      return { knee: away(k1) > away(k2) ? k1 : k2, fx, fy };
    }

    // --- behaviour
    spray(spot, items) {
      const p = this.pos(spot), n = Math.max(3, Math.min(14, Math.round(Math.sqrt(items) * 2.4)));
      let left = items;
      for (let i = 0; i < n; i++) {
        const value = i === n - 1 ? left : Math.floor(items / n); left -= value;
        this.particles.push({ x0: p.x + (Math.random() - .5) * 8, y0: p.y + (Math.random() - .5) * 8, t: -i * .045, dur: .55 + Math.random() * .35,
          bend: (Math.random() - .5) * 90, value });
      }
      this.floats.push({ x: p.x, y: p.y - 10, text: `+${nf.format(items)} item${items === 1 ? "" : "s"}`, t: 0 });
    }
    path(url) { try { const u = new URL(url); return (u.pathname + u.search).slice(0, 60) || "/"; } catch (e) { return url; } }

    update(dt) {
      const sp = this.s, backlog = this.tasks.length;
      if (this.state === "idle" && backlog) {
        if (backlog > 14) for (const t of this.tasks.splice(0, backlog - 4)) this.settle(t, true);  // catch up on a replay
        this.task = this.tasks.shift(); this.state = "walk";
        this.say(`Walking to ${this.path(this.task.spot.url)}`);
      }
      let target = null;
      if (this.state === "walk" || this.state === "work") target = this.pos(this.task.spot);
      else if (this.finished || this.restT > 1.5) target = { x: this.cx, y: this.cy };
      if (this.state === "idle") {
        this.restT += dt;
        if (this.finished && !this.rested && Math.hypot(sp.x - this.cx, sp.y - this.cy) < 6) {
          this.rested = true; this.say(`Resting on the hub · ${nf.format(this.carried)} items carried`);
        }
      } else this.restT = 0;

      const hurry = 1 + Math.min(4, backlog * .35);
      if (target) {
        const dx = target.x - sp.x, dy = target.y - sp.y, d = Math.hypot(dx, dy);
        const max = (this.state === "idle" ? 70 : 165) * hurry * this.scale, want = d < 4 ? 0 : Math.min(max, d * 4);
        const tvx = d ? dx / d * want : 0, tvy = d ? dy / d * want : 0, k = Math.min(1, dt * 7);
        sp.vx += (tvx - sp.vx) * k; sp.vy += (tvy - sp.vy) * k;
        if (this.state === "walk" && d < 5) { this.state = "work"; this.workT = 0; this.say(this.task.status === "refused" ? "Refused — this page is off limits" : this.task.status === "error" ? "The page answered with an error" : `Wrapping ${this.path(this.task.spot.url)}`); }
      } else { sp.vx *= .8; sp.vy *= .8; }
      if (reduced && target && this.state === "walk") { sp.x = target.x; sp.y = target.y; }
      sp.x += sp.vx * dt; sp.y += sp.vy * dt;
      const speed = Math.hypot(sp.vx, sp.vy);
      if (speed > 8) { let dh = Math.atan2(sp.vy, sp.vx) - sp.h; dh = Math.atan2(Math.sin(dh), Math.cos(dh)); sp.h += dh * Math.min(1, dt * 9); }

      if (this.state === "work") {
        this.workT += dt * hurry;
        if (this.workT > .3 && !this.task.finalized) this.settle(this.task, false);
        if (this.workT > (this.task.status === "ok" ? .95 : .6)) { this.state = "idle"; this.task = null; if (!this.tasks.length && !this.finished) this.say("Listening for the next page…"); }
      }
      this.updateLegs(dt);

      for (const p of this.particles) p.t += dt / p.dur;
      for (const p of this.particles) if (p.t >= 1 && !p.landed) { p.landed = true; this.carried += p.value; sp.glow = 1; this.hud.carried(this.carried); }
      this.particles = this.particles.filter((p) => p.t < 1);
      for (const f of this.floats) f.t += dt;
      this.floats = this.floats.filter((f) => f.t < 1.4);
      sp.glow = Math.max(0, sp.glow - dt * 1.6);
    }
    settle(task, quick) {
      task.finalized = true;
      task.spot.status = task.status === "pending" ? "ok" : task.status;
      task.spot.done = performance.now();
      if (task.status === "refused" && !quick) { const a = this.s.h + Math.PI; this.s.vx = Math.cos(a) * 120; this.s.vy = Math.sin(a) * 120; }
      if (!task.items) return;
      if (quick) { this.carried += task.items; this.hud.carried(this.carried); }
      else this.spray(task.spot, task.items);
    }

    // --- drawing
    frame(now) {
      const dt = Math.min(.05, (now - this.last) / 1000); this.last = now;
      this.update(dt);
      const { ctx } = this, t = tokens();
      ctx.clearRect(0, 0, this.w, this.h);
      this.drawWeb(ctx, t, now);
      for (const spot of this.spots.values()) this.drawSpot(ctx, t, spot, now);
      // dragline: the silk the spider trails back to the hub
      const sp = this.s, back = this.local(-18 * this.scale, 0);
      ctx.strokeStyle = t.ink; ctx.globalAlpha = .28; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(this.cx, this.cy); ctx.quadraticCurveTo((this.cx + back.x) / 2, (this.cy + back.y) / 2 + 18, back.x, back.y); ctx.stroke();
      ctx.globalAlpha = 1;
      if (this.state === "work" && this.task.status === "ok") this.drawWrap(ctx, t, this.pos(this.task.spot));
      this.drawSpider(ctx, t, now);
      const abd = this.local(-10 * this.scale, 0);
      for (const p of this.particles) {
        if (p.t < 0) continue;
        const e = 1 - Math.pow(1 - p.t, 3), mx = (p.x0 + abd.x) / 2 + p.bend, my = (p.y0 + abd.y) / 2 - 40;
        const x = (1 - e) * (1 - e) * p.x0 + 2 * (1 - e) * e * mx + e * e * abd.x, y = (1 - e) * (1 - e) * p.y0 + 2 * (1 - e) * e * my + e * e * abd.y;
        ctx.fillStyle = t.lime; ctx.globalAlpha = 1 - p.t * .3; ctx.fillRect(x - 2, y - 2, 4, 4);
      }
      ctx.globalAlpha = 1;
      ctx.font = `500 12px "Geist Mono", monospace`; ctx.textAlign = "center";
      for (const f of this.floats) { ctx.globalAlpha = Math.max(0, 1 - f.t / 1.4); ctx.fillStyle = t.lime; ctx.fillText(f.text, f.x, f.y - f.t * 26); }
      ctx.globalAlpha = 1; ctx.textAlign = "start";
      this.raf = requestAnimationFrame((n) => this.frame(n));
    }
    drawWeb(ctx, t, now) {
      ctx.strokeStyle = t.line; ctx.lineWidth = 1;
      ctx.globalAlpha = .9;
      ctx.beginPath();
      for (let j = 0; j < this.N; j++) { const p = this.at(this.K + .35, j); ctx.moveTo(this.cx, this.cy); ctx.lineTo(p.x, p.y); }
      ctx.stroke();
      ctx.globalAlpha = .7;
      for (let k = .35; k <= this.K; k += k < 1 ? .325 : 1) {
        ctx.beginPath();
        for (let j = 0; j <= this.N; j++) {
          const p = this.at(k, j);
          if (!j) { ctx.moveTo(p.x, p.y); continue; }
          const q = this.at(k, j - 1), mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
          ctx.quadraticCurveTo(mx + (this.cx - mx) * .07, my + (this.cy - my) * .07, p.x, p.y);  // silk sags toward the hub
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    drawSpot(ctx, t, spot, now) {
      const p = this.pos(spot), age = Math.max(0, Math.min(1, (now - spot.born) / 350));
      if (spot.status === "pending") {  // a fly, buzzing
        const flap = Math.sin(now / 28 + spot.wing) * .5 + .5;
        ctx.fillStyle = t.ink3; ctx.globalAlpha = .5 * age;
        ctx.beginPath(); ctx.ellipse(p.x - 3, p.y - 2, 4, 2 + flap * 2, -.6, 0, 6.283); ctx.ellipse(p.x + 3, p.y - 2, 4, 2 + flap * 2, .6, 0, 6.283); ctx.fill();
        ctx.globalAlpha = age; ctx.fillStyle = t.ink; ctx.beginPath(); ctx.ellipse(p.x, p.y, 2.2, 3.4, 0, 0, 6.283); ctx.fill();
      } else if (spot.status === "ok") {  // wrapped in silk
        const pop = Math.max(0, Math.min(1, (now - spot.done) / 400)), r = 4 + (1 - pop) * 6;
        ctx.globalAlpha = .25 * (1 - pop); ctx.fillStyle = t.lime; ctx.beginPath(); ctx.arc(p.x, p.y, r + 8, 0, 6.283); ctx.fill();
        ctx.globalAlpha = 1; ctx.fillStyle = t.lime; ctx.beginPath(); ctx.ellipse(p.x, p.y, 3.4, 5, 0, 0, 6.283); ctx.fill();
        ctx.strokeStyle = t.bg; ctx.lineWidth = .8; ctx.beginPath();
        for (const dy of [-2, 0, 2]) { ctx.moveTo(p.x - 3, p.y + dy - 1); ctx.lineTo(p.x + 3, p.y + dy + 1); }
        ctx.stroke();
      } else {
        const color = spot.status === "refused" ? t.coral : t.amber;
        ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.globalAlpha = 1;
        ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, 6.283); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(p.x - 3, p.y - 3); ctx.lineTo(p.x + 3, p.y + 3); ctx.moveTo(p.x + 3, p.y - 3); ctx.lineTo(p.x - 3, p.y + 3); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    drawWrap(ctx, t, p) {
      const k = this.workT;
      ctx.strokeStyle = t.ink; ctx.lineWidth = 1; ctx.globalAlpha = .55;
      for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.ellipse(p.x, p.y, 7 + i * 3, 4 + i * 2, k * 9 + i * 1.3, 0, Math.PI * 1.4); ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
    drawSpider(ctx, t, now) {
      const sp = this.s, S = this.scale, walking = Math.hypot(sp.vx, sp.vy) > 10;
      const bob = walking ? Math.sin(now / 55) * .6 : Math.sin(now / 600) * .4;
      // shadow
      ctx.globalAlpha = .22; ctx.fillStyle = "#000";
      ctx.beginPath(); ctx.ellipse(sp.x + 4, sp.y + 7, 15 * S, 9 * S, sp.h, 0, 6.283); ctx.fill(); ctx.globalAlpha = 1;
      // legs
      ctx.strokeStyle = t.ink; ctx.lineCap = "round"; ctx.lineJoin = "round";
      for (const leg of sp.legs) {
        const hip = this.local(leg.hipF, leg.hipL);
        let { knee, fx, fy } = this.knee(hip.x, hip.y, leg.x, leg.y, leg.l1, leg.l2);
        if (leg.stepping) { const lift = Math.sin(leg.t * Math.PI) * 4 * S; knee = { x: knee.x + (knee.x - sp.x) / 30 * lift, y: knee.y + (knee.y - sp.y) / 30 * lift }; }
        ctx.lineWidth = 2.4 * S; ctx.beginPath(); ctx.moveTo(hip.x, hip.y); ctx.lineTo(knee.x, knee.y); ctx.stroke();
        ctx.lineWidth = 1.5 * S; ctx.beginPath(); ctx.moveTo(knee.x, knee.y); ctx.lineTo(fx, fy); ctx.stroke();
      }
      ctx.save(); ctx.translate(sp.x, sp.y); ctx.rotate(sp.h);
      // pedipalps
      ctx.lineWidth = 1.4 * S; ctx.beginPath(); ctx.moveTo(8 * S, -2 * S); ctx.lineTo(13 * S, -3.5 * S); ctx.moveTo(8 * S, 2 * S); ctx.lineTo(13 * S, 3.5 * S); ctx.stroke();
      // abdomen, glowing while it takes in data
      ctx.shadowColor = t.lime; ctx.shadowBlur = 22 * sp.glow;
      ctx.fillStyle = t.ink; ctx.beginPath(); ctx.ellipse(-10 * S, 0, (9.5 + bob) * S, (7.4 + bob * .6) * S, 0, 0, 6.283); ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = t.lime; ctx.globalAlpha = .55 + sp.glow * .45;
      for (const [x, w] of [[-6, 2.2], [-10, 3], [-14, 2.1]]) { ctx.beginPath(); ctx.moveTo((x + 1.6) * S, 0); ctx.lineTo((x - 1.4) * S, -w * S); ctx.lineTo((x - .4) * S, 0); ctx.lineTo((x - 1.4) * S, w * S); ctx.closePath(); ctx.fill(); }
      ctx.globalAlpha = 1;
      // cephalothorax and eyes
      ctx.fillStyle = t.ink; ctx.beginPath(); ctx.ellipse(2 * S, 0, 6.4 * S, 5.4 * S, 0, 0, 6.283); ctx.fill();
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
      el("p", { text: "Watch the spider walk the web: every page it reaches is a fly it wraps up, and the items it finds stream home. The map on the left shows how the pages connect." })),
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
      const netCard = el("div", { class: "card net" }, canvas, tip,
        el("div", { class: "nettitle" }, el("div", { class: "eyebrow", text: "crawl map" })),
        el("div", { class: "legend" }, ...[["ok", "fetched"], ["refused", "refused"], ["error", "error"], ["pending", "seen"]].map(([k, l]) => el("span", {}, el("i", { style: `background:var(${{ ok: "--lime", refused: "--coral", error: "--amber", pending: "--ink-3" }[k]})` }), l))));
      feedEl = el("div", { class: "feed", role: "log" });
      const filter = { level: "all" };
      const feedCard = el("div", { class: "card" }, el("h2", {}, "Event feed", el("span", { style: "margin-left:auto" }, seg([["all", "All"], ["WARNING", "Warnings"]], "all", (v) => { filter.level = v; $$(".ev", feedEl).forEach((n) => { n.hidden = v !== "all" && n.classList.contains("INFO"); }); }))), feedEl);
      feedEl.filter = filter;
      const action = el("div", { class: "spider-action", text: "" });
      const carried = el("div", { class: "num", text: "0" });
      const spiderCanvas = el("canvas", { "aria-label": "Animated spider walking the crawl" });
      const spiderCard = el("div", { class: "card spider-stage" }, spiderCanvas,
        el("div", { class: "spider-hud" }, el("div", {}, el("div", { class: "eyebrow", text: "the crawler" }), action),
          el("div", { class: "carried" }, el("div", { class: "eyebrow", text: "carried home" }), carried)),
        el("div", { class: "legend" }, ...[["--ink-3", "fly = page found"], ["--lime", "wrapped = data taken"], ["--coral", "refused"], ["--amber", "error"]].map(([c, l]) => el("span", {}, el("i", { style: `background:var(${c})` }), l))));
      mapSlot.replaceChildren(netCard);
      monitor.replaceChildren(head, banner,
        el("div", { class: "gauges" }, el("div", { class: "card ring" }, circle, el("div", { class: "lbl" }, ringVal, el("span", { text: "pages" }))), gItems, gQueue, gRef),
        spiderCard, feedCard);
      net = new Constellation(canvas, tip);
      spider = new SpiderScene(spiderCanvas, {
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
