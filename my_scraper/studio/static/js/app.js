/* Seine Studio: the router and the keyboard shortcuts. No build step. */

import { $, $$, wait, reduced, el, icon, debounce } from "./ui.js";
import { go } from "./shell.js";
import { pollJobs } from "./jobs.js";
import { closeDrawer } from "./records.js";
import { closePalette, openPalette, paletteOpen, toggleTheme } from "./palette.js";
import { overview } from "./views/overview.js";
import { extract } from "./views/extract.js";
import { live } from "./views/live.js";
import { library } from "./views/library.js";
import { run } from "./views/run.js";

const VIEWS = { overview, extract, live, library, run };

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

const decode = (text) => { try { return decodeURIComponent(text); } catch (e) { return text; } };  // a hand-typed % is no error

async function route() {
  const token = ++routeToken;
  const [path, qs] = (location.hash.slice(1) || "/").split("?");
  const parts = path.split("/").filter(Boolean).map(decode);
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

// ---------------------------------------------------------------- shortcuts
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); paletteOpen() ? closePalette() : openPalette(); return; }
  if (e.key === "Escape") { if (paletteOpen()) closePalette(); else closeDrawer(); return; }
  if (e.target.matches("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey || paletteOpen()) return;
  if (e.key === "/") { const s = $("#librarySearch"); if (s) { e.preventDefault(); s.focus(); } }
  else if (e.key === "n" || e.key === "N") go("#/extract");
  else if (e.key === "t" || e.key === "T") toggleTheme();
  else if (["1", "2", "3", "4"].includes(e.key)) go(["#/", "#/extract", "#/live", "#/library"][+e.key - 1]);
});

window.addEventListener("hashchange", route);
window.addEventListener("resize", debounce(() => setNav($("#nav a.active")?.dataset.view), 100));
setInterval(() => { if (!document.hidden) pollJobs(); }, 2500);  // a hidden tab catches up on its next poll
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollJobs(); });
pollJobs();
route();
