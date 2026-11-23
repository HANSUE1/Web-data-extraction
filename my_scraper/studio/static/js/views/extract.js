/* Extract: the form that starts a crawl, with a quick look at the first page. */

import { $, $$, reduced, el, icon, api, toast, store, copy, seg, tabs } from "../ui.js";
import { crumbs, go } from "../shell.js";
import { pollJobs } from "../jobs.js";
import { markdown, recordTable } from "../records.js";

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

export const extract = async (root, _parts, ctx) => {
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
  const recipes = el("div", { class: "recipes" }, ...recipeDefs.map(([key, ic, title, text]) => el("button", { type: "button", class: "recipe" + (f.recipe === key ? " on" : ""), "aria-pressed": String(f.recipe === key),
    onclick: (e) => { f.recipe = key; $$(".recipe", recipes).forEach((r) => { r.classList.toggle("on", r === e.currentTarget); r.setAttribute("aria-pressed", String(r === e.currentTarget)); }); renderFields(); readFields(); save(); } },
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
  const exportChips = el("div", { class: "chips" }, ...["jsonl", "csv", "json", "sqlite"].map((fmt) => el("button", { type: "button", class: "chip" + (f.export.includes(fmt) ? " on" : ""), "aria-pressed": String(f.export.includes(fmt)), text: fmt.toUpperCase(),
    onclick: (e) => { const on = !f.export.includes(fmt); if (!on && f.export.length === 1) { toast("Keep at least one format", true); return; } f.export = on ? [...f.export, fmt] : f.export.filter((x) => x !== fmt); e.currentTarget.classList.toggle("on", on); e.currentTarget.setAttribute("aria-pressed", String(on)); save(); } })));

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
    launchBtn.classList.add("busy"); launchBtn.disabled = true;  // a second click would start a second crawl
    try {
      const job = await api("/api/jobs", { method: "POST", body: p });
      toast("Net cast — watching it live");
      pollJobs();
      go(`#/live/${job.id}`);
    } catch (err) { toast(err.message, true); } finally { launchBtn.classList.remove("busy"); launchBtn.disabled = false; }
  });

  const side = el("aside", { class: "side" },
    el("div", { class: "card" }, el("h2", {}, "Ready when you are"), el("p", { class: "hint", text: "Robots.txt is obeyed, sites are paced, logins and paywalls are never crossed." }),
      el("div", { style: "display:flex;flex-direction:column;gap:10px;margin-top:14px" }, launchBtn, lookBtn)),
    el("div", { class: "card" }, el("h2", {}, "Same thing, from a terminal", el("button", { class: "btn ghost small", style: "margin-left:auto", onclick: () => copy(cmd.textContent) }, icon("copy"), "Copy")),
      el("div", { style: "margin-top:12px" }, cmd)),
    el("button", { class: "btn ghost small", onclick: () => { store.set("form", {}); go(location.hash); toast("Form reset"); } }, "Reset the form"));

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
