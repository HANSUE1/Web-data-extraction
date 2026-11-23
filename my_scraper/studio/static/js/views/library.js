/* Library: every run in the results folder, searchable and sortable. */

import { $$, nf, el, icon, toast, debounce, store, seg } from "../ui.js";
import { state, crumbs, loadRuns, openFolder, runCard } from "../shell.js";

export const library = async (root, _parts, ctx) => {
  crumbs("Seine", "Library");
  const prefs = Object.assign({ sort: "new", layout: "grid" }, store.get("library", {}));
  const q = el("input", { class: "input", placeholder: "Search runs, sites, files…  ( / )", id: "librarySearch", "aria-label": "Search runs" });
  const siteSel = el("select", { class: "input", "aria-label": "Filter by site" }, el("option", { value: "", text: "All sites" }));
  const sortSel = el("select", { class: "input", "aria-label": "Sort runs" }, ...[["new", "Newest first"], ["old", "Oldest first"], ["items", "Most items"], ["size", "Largest"], ["site", "Site A–Z"]].map(([v, t]) => el("option", { value: v, text: t, selected: v === prefs.sort })));
  const grid = el("div", { class: "runs" + (prefs.layout === "list" ? " list" : "") });
  const countNote = el("span", { class: "hint" });
  root.append(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "The ", el("em", { text: "library" })),
      el("p", { text: "Every run is a folder in outputs/results. Open one to search, sort and inspect its records." })),
      el("div", { class: "actions" }, el("button", { class: "btn", onclick: () => openFolder() }, icon("folder"), "Open folder"),
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
