/* Seine Studio: the command palette and the theme switch. */

import { $, $$, reduced, el, icon, ago, coverPage } from "./ui.js";
import { go, loadRuns, openFolder, runHash } from "./shell.js";

const palette = $("#palette"), pInput = $("#paletteInput"), pList = $("#paletteList");
let pItems = [], pIndex = 0;
export function toggleTheme() {
  const now = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  // stored raw, not as JSON: the inline script in index.html reads it before this file loads
  const apply = () => { document.documentElement.dataset.theme = now; try { localStorage.setItem("seine.theme", now); } catch (e) { /* private window */ } };
  if (document.startViewTransition && !reduced) document.startViewTransition(apply); else apply();
}

let release = null;
export async function openPalette() {
  if (!palette.hidden) return;
  release = coverPage();
  palette.hidden = false; pInput.value = ""; pInput.focus();
  const base = [
    { g: "Go", t: "Overview", i: "home", run: () => go("#/") },
    { g: "Go", t: "New extraction", i: "bolt", k: "N", run: () => go("#/extract") },
    { g: "Go", t: "Live crawls", i: "radar", run: () => go("#/live") },
    { g: "Go", t: "Library", i: "books", run: () => go("#/library") },
    { g: "Do", t: "Switch light / dark", i: "moon", k: "T", run: toggleTheme },
    { g: "Do", t: "Open the results folder", i: "folder", run: () => openFolder() },
  ];
  pItems = base; drawPalette();
  try { const runs = await loadRuns(); pItems = base.concat(runs.map((r) => ({ g: "Run", t: `${r.site} — ${r.items ?? "?"} items`, s: ago(r.created), i: "page", hay: r.name, run: () => go(runHash(r.id)) }))); drawPalette(); } catch (e) { /* palette still works */ }
}
function fuzzy(hay, needle) { let i = 0; hay = hay.toLowerCase(); for (const c of needle.toLowerCase()) { i = hay.indexOf(c, i); if (i < 0) return false; i++; } return true; }
function drawPalette() {
  const n = pInput.value.trim();
  const shown = pItems.filter((it) => !n || fuzzy(it.t + " " + (it.hay || ""), n)).slice(0, 40);
  pIndex = Math.min(pIndex, Math.max(0, shown.length - 1));
  pList.replaceChildren(...shown.map((it, i) => el("li", { class: i === pIndex ? "on" : "", role: "option", "aria-selected": String(i === pIndex), onmouseenter: () => { pIndex = i; $$("li", pList).forEach((li, k) => { li.classList.toggle("on", k === i); li.setAttribute("aria-selected", String(k === i)); }); }, onclick: () => { closePalette(); it.run(); } },
    icon(it.i), el("span", { class: "grp", text: it.g }), el("span", { text: it.t }), it.s || it.k ? el("small", { text: it.s || it.k }) : null)));
  pList.shown = shown;
}
export function closePalette() { if (palette.hidden) return; palette.hidden = true; release(); }
export const paletteOpen = () => !palette.hidden;
pInput.addEventListener("input", () => { pIndex = 0; drawPalette(); });
pInput.addEventListener("keydown", (e) => {
  const shown = pList.shown || [];
  if (e.key === "ArrowDown") { e.preventDefault(); pIndex = (pIndex + 1) % Math.max(1, shown.length); drawPalette(); $("li.on", pList)?.scrollIntoView({ block: "nearest" }); }
  if (e.key === "ArrowUp") { e.preventDefault(); pIndex = (pIndex - 1 + shown.length) % Math.max(1, shown.length); drawPalette(); $("li.on", pList)?.scrollIntoView({ block: "nearest" }); }
  if (e.key === "Enter" && shown[pIndex]) { e.preventDefault(); closePalette(); shown[pIndex].run(); }
});
palette.addEventListener("click", (e) => { if (e.target === palette) closePalette(); });
$("#paletteBtn").addEventListener("click", openPalette);
$("#searchTrigger").addEventListener("click", openPalette);
$("#themeBtn").addEventListener("click", toggleTheme);
