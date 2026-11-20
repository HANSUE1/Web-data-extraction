/* Seine Studio: router, views, jobs poller and command palette. No build step. */

import { $, $$, wait, reduced, nf, el, icon, api, toast, confirmBox, fmtBytes, ago, clock, mark, countUp, debounce, store, copy, seg, tabs } from "./ui.js";
import { heroNet, SilkMap } from "./radar.js";
import { pageStatus, PageWalk } from "./spider.js";

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
  include: "", exclude: "", fields: [], examples: [], look: null };

/** The fields a look at a page found, most common first, each with a short sample value. */
function fieldsOf(data) {
  const count = new Map();
  for (const item of data.items || []) for (const [k, v] of Object.entries(item)) {
    if (k === "source" || v == null || v === "" || (Array.isArray(v) && !v.length)) continue;
    const c = count.get(k) || { n: 0, sample: v }; c.n++; count.set(k, c);
  }
  if (!count.has("url")) count.set("url", { n: 0, sample: data.final_url || data.url });  // every crawled item carries one
  return [...count].sort((a, b) => b[1].n - a[1].n).map(([k, c]) => [k, (typeof c.sample === "object" ? JSON.stringify(c.sample) : String(c.sample)).slice(0, 140)]);
}
const FIELD_LABELS = { url: "Link", text: "All its text", image: "Image" };
const fieldLabel = (name) => FIELD_LABELS[name] || name.charAt(0).toUpperCase() + name.slice(1).replace(/[_-]+/g, " ");

VIEWS.extract = async (root, _parts, ctx) => {
  crumbs("Seine", "Extract");
  const f = Object.assign(structuredClone(DEFAULT_FORM), store.get("form", {}));
  if (!["auto", "exact"].includes(f.recipe)) f.recipe = f.recipe === "examples" ? "exact" : "auto";  // forms saved before the two-choice step
  f.examples = f.examples.filter(([k, v]) => k.trim() && v.trim());  // only finished ones are kept now
  const save = () => { store.set("form", f); renderCmd(); };
  const firstUrl = () => f.urls.split("\n").map((x) => x.trim()).find(Boolean) || "";
  const renderMode = () => (f.mode === "static" ? "never" : f.mode === "browser" ? "always" : "auto");

  let urlTimer = null;
  const urls = el("textarea", { class: "input", rows: 2, placeholder: "https://example.com/catalogue\nOne address per line — every one is a starting point", value: f.urls,
    oninput: (e) => { f.urls = e.target.value; save(); clearTimeout(urlTimer); urlTimer = setTimeout(() => { if (f.recipe === "exact") { renderFields(); readFields(); } }, 800); } });
  const recipeDefs = [
    ["auto", "sparkles", "Auto-detect", "Finds lists, products, articles and tables by itself, and keeps every field."],
    ["exact", "wand", "Pick exact fields", "See what's on the page, then tick only what you want to keep."],
  ];
  const fieldsBox = el("div");
  const recipes = el("div", { class: "recipes" }, ...recipeDefs.map(([key, ic, title, text]) => el("button", { type: "button", class: "recipe" + (f.recipe === key ? " on" : ""),
    onclick: (e) => { f.recipe = key; $$(".recipe", recipes).forEach((r) => r.classList.toggle("on", r === e.currentTarget)); renderFields(); readFields(); save(); } },
    icon(ic), el("b", { text: title }), el("small", { text }))));

  // --- picking fields: the first page is read as soon as there is one to read
  let reading = null, teachOpen = false;
  async function readFields(force = false) {
    const url = firstUrl();
    if (f.recipe !== "exact" || !/^https?:\/\/\S+$/i.test(url) || reading === url || (!force && f.look?.url === url)) return;
    reading = url; renderFields();
    try {
      const data = await api("/v1/scrape", { method: "POST", body: { url, render: renderMode(), max_chars: 40000 } });
      if (ctx.alive() && firstUrl() === url) { f.look = { url, fields: fieldsOf(data) }; save(); }
    } catch (err) {
      if (ctx.alive() && firstUrl() === url) f.look = { url, error: err.message };
    } finally {
      if (reading === url) reading = null;
      if (ctx.alive()) renderFields();
    }
  }

  function renderFields() {
    if (f.recipe !== "exact") { fieldsBox.replaceChildren(); return; }
    const url = firstUrl(), look = f.look?.url === url ? f.look : null, taught = f.examples.length > 0;
    let host = url; try { host = new URL(url).host; } catch (e) { /* shown as typed */ }
    const again = el("button", { class: "btn ghost small", type: "button", onclick: () => readFields(true) }, icon("eye"), "Read again");
    let bar, body;
    if (!/^https?:\/\/\S+$/i.test(url)) {
      bar = el("b", { text: "Fields to keep" });
      body = el("p", { class: "pick-empty", text: url ? "Start the address with http:// or https:// and its fields show up here." : "Add an address above. Everything that page offers shows up here to tick." });
    } else if (reading === url || !look) {
      bar = el("b", { text: `Reading ${host}…` });
      body = el("div", { class: "fieldgrid", "aria-busy": "true" }, ...Array.from({ length: 6 }, () => el("div", { class: "skeleton fpick-ghost" })));
    } else if (look.error) {
      bar = el("b", { text: `Couldn't read ${host}` });
      body = el("div", { class: "pick-empty" }, el("span", { text: look.error }), " ", again);
    } else {
      const names = [...new Set([...look.fields.map(([n]) => n), ...f.fields])];
      const count = el("span", { class: "hint" });
      const paintCount = () => {
        const n = names.filter((x) => f.fields.includes(x)).length;
        count.textContent = taught ? "Ticks are set aside while you teach fields" : n ? `${n} of ${names.length} ticked · only these are saved` : "Tick what you want to keep";
      };
      const all = names.every((x) => f.fields.includes(x));
      bar = el("div", { class: "pick-title" }, el("b", { text: `Fields on ${host}` }), count);
      body = el("div", { class: "fieldgrid" + (taught ? " muted" : "") }, ...names.map((name) => {
        const sample = look.fields.find(([n]) => n === name)?.[1];
        const on = () => f.fields.includes(name);
        return el("button", { type: "button", class: "fpick" + (on() ? " on" : ""), role: "checkbox", "aria-checked": String(on()), title: sample || null, disabled: taught,
          onclick: (e) => { f.fields = on() ? f.fields.filter((x) => x !== name) : [...f.fields, name]; e.currentTarget.classList.toggle("on", on()); e.currentTarget.setAttribute("aria-checked", String(on())); paintCount(); save(); } },
          el("span", { class: "box" }, icon("check")), el("b", { text: fieldLabel(name) }), el("small", { text: sample || "not on this page" }));
      }));
      paintCount();
      bar = el("div", { class: "pick-bar" }, bar,
        taught ? null : el("button", { class: "btn ghost small", type: "button", onclick: () => { f.fields = all ? [] : names; save(); renderFields(); } }, all ? "Clear all" : "Tick all"), again);
    }
    if (!bar.classList.contains("pick-bar")) bar = el("div", { class: "pick-bar" }, bar);
    fieldsBox.replaceChildren(...[bar, taught ? taughtNote() : null, body, teachBox(taught)].filter(Boolean));
  }

  function taughtNote() {
    return el("div", { class: "pick-note" }, icon("wand"),
      el("span", { text: "Using the fields you taught: the crawl takes only those, from every item like the one you copied from." }),
      el("button", { class: "btn ghost small", type: "button", onclick: () => { f.examples = []; teachOpen = false; save(); renderFields(); } }, "Go back to ticking"));
  }

  function teachBox(taught) {
    if (!taught && !teachOpen) {
      return el("div", { class: "teach" }, el("button", { class: "btn ghost small", type: "button", onclick: () => { teachOpen = true; renderFields(); $(".teach input", fieldsBox)?.focus(); } },
        icon("plus"), "Something missing? Teach it a field"));
    }
    const name = el("input", { class: "input", placeholder: "Name it, e.g. author", "aria-label": "Field name" });
    const value = el("input", { class: "input", placeholder: "Paste a value you see on the page, e.g. Shel Silverstein", "aria-label": "A value from the page" });
    const add = () => {
      if (!name.value.trim()) { name.focus(); return; }
      if (!value.value.trim()) { value.focus(); return; }
      f.examples = [...f.examples.filter(([k]) => k !== name.value.trim()), [name.value.trim(), value.value.trim()]];
      save(); renderFields(); $(".teach input", fieldsBox)?.focus();
    };
    for (const input of [name, value]) input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    return el("div", { class: "teach" },
      el("p", { class: "hint", text: "Pick one item on the page. Copy a value from it exactly as shown, and give it a name. It works out where that value sits and takes the same thing from every item. Teach each field you want." }),
      taught ? el("div", { class: "chips" }, ...f.examples.map(([k, v]) => el("span", { class: "chip on taught" }, el("b", { text: k }), el("small", { text: v }),
        el("button", { class: "icon-btn", type: "button", "aria-label": `Forget ${k}`, onclick: () => { f.examples = f.examples.filter(([n]) => n !== k); save(); renderFields(); } }, icon("x"))))) : null,
      el("div", { class: "teach-row" }, name, value, el("button", { class: "btn small", type: "button", onclick: add }, icon("wand"), "Teach")));
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
    if (f.recipe === "exact") {
      const taught = pairs(f.examples);
      if (Object.keys(taught).length) p.examples = taught;
      else if (f.fields.length) p.fields = f.fields;
    }
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
    if (p.fields) parts.push(["--fields", p.fields.map(q).join(" ")]);
    for (const [k, v] of Object.entries(p.examples || {})) parts.push(["--example", q(`${k}=${v}`)]);
    parts.push(["--export", p.export.join(" ")]);
    cmd.replaceChildren(...parts.flatMap(([k, v], i) => [i ? " " : "", el("span", { class: i ? "k" : "", text: k }), v != null ? " " : "", v != null ? el("span", { class: "v", text: String(v) }) : ""]));
  }
  renderCmd();

  const launchBtn = el("button", { class: "btn primary launch" }, icon("bolt"), "Cast the net");
  const lookBtn = el("button", { class: "btn", style: "width:100%" }, icon("eye"), "Quick look at the first page");
  const previewBox = el("div");

  async function quickLook() {
    const first = payload().urls[0];
    if (!first) { urls.focus(); toast("Add an address first", true); return; }
    lookBtn.classList.add("busy"); lookBtn.disabled = launchBtn.disabled = true;
    previewBox.replaceChildren(el("div", { class: "card skeleton preview", style: "height:260px" }));
    try {
      const data = await api("/v1/scrape", { method: "POST", body: { url: first, render: renderMode(), max_chars: 40000 } });
      if (!ctx.alive()) return;
      f.look = { url: first, fields: fieldsOf(data) }; save(); renderFields();  // the picker needn't read it again
      previewBox.replaceChildren(previewCard(data));
      previewBox.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    } catch (err) {
      previewBox.replaceChildren(el("div", { class: "done-banner bad preview" }, el("h3", { text: "Couldn't look" }), el("span", { text: err.message })));
    } finally { lookBtn.classList.remove("busy"); lookBtn.disabled = launchBtn.disabled = false; }
  }
  lookBtn.addEventListener("click", quickLook);
  launchBtn.addEventListener("click", async () => {
    const p = payload();
    if (!p.urls.length) { urls.focus(); toast("Add at least one address", true); return; }
    if (f.recipe === "exact" && !p.fields && !p.examples) {
      toast("Tick at least one field to keep", true);
      fieldsBox.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
      readFields();
      return;
    }
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

  root.append(el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Cast a ", el("em", { text: "net" })),
    el("p", { text: "Point it at a public site, say what you're after, and let it work. Everything lands in your Library." }))));
  root.append(el("div", { class: "extract" }, el("div", {}, steps, previewBox), side));
  if (ctx.params.get("look") && f.urls.trim()) quickLook();
  else if (!f.urls) urls.focus();
  else readFields();
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
  root.append(el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "The net, ", el("em", { text: "live" })),
    el("p", { text: "Watch the spider walk each page it crawls: it drops in on a thread, wraps every item it takes and taps the links it will follow next. The silk map beside it is the site as a web: each page hangs from its parent path, sections cluster together, and every fetch sends a pulse down the strands to that page." })),
    el("div", { class: "actions" }, el("a", { class: "btn primary", href: "#/extract" }, icon("bolt"), "New extraction"))));
  const listBox = el("div", { class: "jobs" });
  const monitor = el("div", { class: "monitor" });
  root.append(el("div", { class: "live" }, el("div", { class: "live-left" }, listBox), monitor));

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
      el("div", { class: "nettitle" }, el("div", { class: "eyebrow", text: "silk map" }), radarStats),
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
    monitor.replaceChildren(head, banner,
      el("div", { class: "gauges" }, el("div", { class: "card ring" }, circle, el("div", { class: "lbl" }, ringVal, el("span", { text: "pages" }))), gItems, gQueue, gRef),
      el("div", { class: "live-stage" }, spiderCard, netCard), feedCard);
    net = new SilkMap(canvas, tip, radarStats);
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
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "The ", el("em", { text: "library" })),
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
  let rows = [], columns = [], matched = 0, total = 0, loading = false, selectedRow = -1, loadToken = 0;

  const dl = el("a", { class: "btn", href: "#" }, icon("download"), "Download");
  const header = el("div", { class: "page-head" },
    el("div", { style: "display:flex;gap:16px;align-items:center" }, mark(run.site),
      el("div", {}, el("h1", { style: "font-size:46px", text: run.site }), el("p", { class: "hint", style: "margin:4px 0 0", text: `${new Date(run.created * 1000).toLocaleString()} · ${fmtBytes(run.size)} · ${run.name}` }))),
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
setInterval(() => { if (!document.hidden) pollJobs(); }, 2500);  // a hidden tab catches up on its next poll
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollJobs(); });

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
