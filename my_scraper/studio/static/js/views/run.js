/* One run: its files as a table, cards or JSON, with search, sort and columns. */

import { $, $$, nf, el, icon, api, fmtBytes, mark, debounce, store, seg, tabs } from "../ui.js";
import { crumbs, deleteRun, go, loadRuns, openFolder, runHash } from "../shell.js";
import { cell, highlight, jsonView, openDrawer, opens } from "../records.js";

export const run = async (root, parts, ctx) => {
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
  let rows = [], columns = [], matched = 0, total = 0, loading = false, selectedRow = -1, loadToken = 0;

  const dl = el("a", { class: "btn", href: "#" }, icon("download"), "Download");
  const header = el("div", { class: "page-head" },
    el("div", { style: "display:flex;gap:16px;align-items:center" }, mark(run.site),
      el("div", {}, el("h1", { style: "font-size:46px", text: run.site }), el("p", { class: "hint", style: "margin:4px 0 0", text: `${new Date(run.created * 1000).toLocaleString()} · ${fmtBytes(run.size)} · ${run.name}` }))),
    el("div", { class: "actions" }, dl,
      el("button", { class: "btn", onclick: () => openFolder(run.id) }, icon("folder"), "Folder"),
      run.id ? el("button", { class: "btn danger", onclick: async () => { if (await deleteRun(run)) go("#/library"); } }, icon("trash"), "Delete") : null));
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
    if (loading && !reset) return;  // "load more" waits; a new search, sort or file replaces what is loading
    const token = ++loadToken;
    loading = true;
    if (reset) { rows = []; selectedRow = -1; body.replaceChildren(el("div", { class: "skeleton", style: "height:360px" })); }
    dl.href = "/api/download?path=" + encodeURIComponent(file.path);
    try {
      const data = await api("/api/file", { query: { path: file.path, offset: rows.length, limit: 150, q: opts.q, sort: opts.sort, desc: opts.desc ? 1 : "" } });
      if (!ctx.alive() || token !== loadToken) return;
      if (data.kind === "text") {
        toolbar.hidden = true; pager.replaceChildren();
        body.replaceChildren(el("pre", { class: "code", text: data.text + (data.truncated ? "\n\n… (first 1 MB shown — download for the rest)" : "") }));
        return;
      }
      toolbar.hidden = false;
      columns = data.columns; matched = data.matched; total = data.total;
      rows = rows.concat(data.rows);
      paint(reset ? 0 : rows.length - data.rows.length);
    } catch (err) { if (token === loadToken) body.replaceChildren(el("div", { class: "done-banner bad" }, el("h3", { text: "Couldn't read this file" }), el("span", { text: err.message }))); }
    finally { if (token === loadToken) loading = false; }
  }

  const visible = () => columns.filter((c) => !opts.hidden[c]);
  function paint(from = 0) {
    const cols = visible(), needle = opts.q;
    if (!rows.length) {
      body.replaceChildren(el("div", { class: "empty card" }, icon("search"), el("h3", { text: total ? "No matches" : "Empty file" }), el("p", { text: total ? "Nothing in this file contains that text." : "This run caught nothing." })));
    } else if (opts.mode === "table") {
      let tbody = $("tbody", body);
      if (!tbody || from === 0) {
        const sortBy = (c) => { if (opts.sort === c) { if (opts.desc) { opts.sort = null; opts.desc = false; } else opts.desc = true; } else { opts.sort = c; opts.desc = false; } load(true); };
        const head = el("tr", {}, el("th", { class: "idx", text: "#" }), ...cols.map((c) => el("th", { class: opts.sort === c && opts.desc ? "desc" : "", title: "Sort by " + c, tabindex: 0,
          "aria-sort": opts.sort !== c ? "none" : opts.desc ? "descending" : "ascending",
          onclick: () => sortBy(c), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(c); } } },
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
        grid.append(el("article", { class: "rec", style: `animation-delay:${Math.min(k, 24) * 20}ms`, ...opens(r, `Record ${i + 1}`) },
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
    if (e.key === "Enter" && selectedRow >= 0 && !e.target.closest("button, a, th")) { e.preventDefault(); openDrawer(rows[selectedRow], `Record ${selectedRow + 1}`); }
  };
  document.addEventListener("keydown", onKey);
  load(true);
  return () => { document.removeEventListener("keydown", onKey); io?.disconnect(); };
};
