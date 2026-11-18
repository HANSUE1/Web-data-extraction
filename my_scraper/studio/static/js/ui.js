/* Seine Studio: DOM, network and formatting helpers shared by every view. */


// ---------------------------------------------------------------- helpers
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
export const nf = new Intl.NumberFormat();

/** Build an element. Text always goes in as text, never as HTML. */
export function el(tag, props = {}, ...kids) {
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
export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.innerHTML = ICONS[name] || "";  // constant markup from the table above
  return svg;
}

export async function api(path, { method = "GET", body, query } = {}) {
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

export function toast(message, bad = false, onClick) {
  const node = el("div", { class: "toast" + (bad ? " bad" : ""), role: "status" }, el("i"), el("span", { text: message }));
  if (onClick) { node.style.cursor = "pointer"; node.addEventListener("click", onClick); }
  $("#toasts").append(node);
  setTimeout(() => { node.classList.add("out"); setTimeout(() => node.remove(), 320); }, bad ? 6000 : 3600);
}

export function confirmBox(title, text, okText = "Delete") {
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

export const fmtBytes = (n) => { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB"]; const i = Math.min(3, Math.floor(Math.log(n) / Math.log(1024))); return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`; };
export function ago(seconds) {
  const d = Date.now() / 1000 - seconds;
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 86400 * 7) return `${Math.round(d / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
export const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString(undefined, { hour12: false });
/** CSS colour tokens for canvases, re-read after a theme switch. */
let tokenCache = null;
export function tokens() {
  const theme = document.documentElement.dataset.theme || "auto";
  if (!tokenCache || tokenCache.theme !== theme) {
    const css = getComputedStyle(document.documentElement), v = (n) => css.getPropertyValue(n).trim();
    tokenCache = { theme, line: v("--line-2"), lime: v("--lime"), coral: v("--coral"), amber: v("--amber"), ink: v("--ink"), ink3: v("--ink-3"), bg: v("--panel"), tide: v("--tide") };
  }
  return tokenCache;
}
export function hue(text) { let h = 0; for (const c of String(text)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
export function mark(site) {
  const h = hue(site);
  return el("span", { class: "mono-mark", style: `background:hsl(${h} 70% 72%)`, text: (String(site).replace(/^www\./, "")[0] || "?").toUpperCase() });
}

export function countUp(node, to, { suffix = "", decimals = 0 } = {}) {
  if (reduced) { node.firstChild.textContent = nf.format(to); return; }
  const start = performance.now(), dur = 1100;
  const step = (now) => {
    const p = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - p, 4);
    node.firstChild.textContent = decimals ? (to * e).toFixed(decimals) : nf.format(Math.round(to * e));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export const store = {
  get(key, fallback) { try { const v = localStorage.getItem("seine." + key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem("seine." + key, JSON.stringify(value)); } catch (e) { /* private window */ } },
};
export async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { toast("Could not copy", true); }
}

/** Segmented control with a sliding thumb. */
export function seg(options, value, onChange) {
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

export function tabs(items, active, onChange) {
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
