/* Seine Studio: showing scraped records. Cells, the record drawer, small tables, Markdown and JSON. */

import { $, reduced, el, icon, copy, coverPage } from "./ui.js";

const isUrl = (v) => typeof v === "string" && /^https?:\/\/\S+$/.test(v);
export function highlight(text, needle) {
  if (!needle) return [text];
  const out = [], lower = text.toLowerCase(), n = needle.toLowerCase();
  let i = 0, j;
  while ((j = lower.indexOf(n, i)) !== -1 && out.length < 40) { out.push(text.slice(i, j), el("mark", { text: text.slice(j, j + n.length) })); i = j + n.length; }
  out.push(text.slice(i));
  return out;
}
export function cell(value, needle) {
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

let releaseDrawer = null, closing = 0;
export function openDrawer(record, title) {
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
  clearTimeout(closing);  // opened again while still sliding shut
  drawer.hidden = false; drawer.classList.remove("closing");
  releaseDrawer ||= coverPage();
  $("#drawerClose").focus();
}
export function closeDrawer() {
  const drawer = $("#drawer");
  if (drawer.hidden || drawer.classList.contains("closing")) return;
  drawer.classList.add("closing");
  releaseDrawer?.(); releaseDrawer = null;
  closing = setTimeout(() => { drawer.hidden = true; drawer.classList.remove("closing"); }, reduced ? 0 : 280);
}
$("#drawerClose").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });

/** Open a record from the keyboard as well as by a click. */
export const opens = (record, title) => ({ tabindex: 0, onclick: () => openDrawer(record, title),
  onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) { e.preventDefault(); e.stopPropagation(); openDrawer(record, title); } } });  // or the same Enter presses the drawer's Close

export function recordTable(rows) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return el("div", { class: "tablewrap", style: "max-height:420px" }, el("table", { class: "data" },
    el("thead", {}, el("tr", {}, el("th", { class: "idx", text: "#" }), ...cols.map((c) => el("th", { text: c })))),
    el("tbody", {}, ...rows.map((r, i) => el("tr", { style: `animation-delay:${Math.min(i, 20) * 18}ms`, ...opens(r, `Item ${i + 1}`) },
      el("td", { class: "idx", text: i + 1 }), ...cols.map((c) => el("td", {}, cell(r[c]))))))));
}

/** Tiny Markdown: headings, lists, paragraphs, links, bold, code. Built as DOM, never HTML. */
export function markdown(text) {
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

export function jsonView(data) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const text = esc(JSON.stringify(data, null, 2) || "");
  const pre = el("pre", { class: "code" });
  // input is escaped above, so only these spans are markup
  pre.innerHTML = text.replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi,
    (m, str, colon, bool, num) => str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`) : bool ? `<span class="b">${bool}</span>` : `<span class="n">${num}</span>`);
  return pre;
}
