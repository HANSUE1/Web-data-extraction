/* Overview: the address bar, totals, the last 30 days and the latest runs. */

import { $, nf, el, icon, api, toast, mark, countUp, store } from "../ui.js";
import { crumbs, go, runCard } from "../shell.js";
import { heroNet } from "../radar.js";

export const overview = async (root, _parts, ctx) => {
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
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("preserveAspectRatio", "none");
  let markup = "";
  for (const f of [.25, .5, .75, 1]) markup += `<line class="gridline" x1="0" x2="${W}" y1="${H - f * H}" y2="${H - f * H}"/>`;
  days.forEach((d, i) => {
    const bh = d.items ? Math.max(4, (d.items / max) * (H - 10)) : 2;
    markup += `<g class="col" data-i="${i}"><rect class="bar ${d.items ? "has" : ""} ${i === days.length - 1 ? "today" : ""}" x="${i * bw + 3}" y="${H - bh}" width="${bw - 6}" height="${bh}" style="animation-delay:${i * 22}ms"/><rect class="hit" x="${i * bw}" y="0" width="${bw}" height="${H}"/></g>`;
  });
  svg.innerHTML = markup;  // numbers only
  // beside the drawing, not in it: the bars stretch with the card and would stretch the letters too
  const axis = el("div", { class: "chart-axis" }, ...[0, 14, 29].map((i) => el("span", { text: new Date(days[i].day + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) })));
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
  const sites = el("ul", { class: "sites" }, ...stats.top_sites.map(([site, n], i) => el("li", {}, el("a", { href: `#/library?site=${encodeURIComponent(site)}` },
    mark(site), el("span", { text: site }), el("b", { text: nf.format(n) }),
    el("div", { class: "meter" }, el("i", { style: `width:${(n / topMax) * 100}%;animation-delay:${200 + i * 90}ms` }))))));
  lower.replaceChildren(
    el("div", { class: "card chart-card" }, el("h2", {}, "Catch, last 30 days", el("span", { class: "eyebrow", text: `${nf.format(total30)} items` })), chartBox, axis),
    el("div", { class: "card" }, el("h2", {}, "Richest waters", el("span", { class: "eyebrow", text: "items by site" })),
      stats.top_sites.length ? sites : el("p", { class: "hint", text: "Nothing caught yet." })));

  recentWrap.append(el("div", { class: "page-head", style: "margin:0 0 16px" }, el("h2", { style: "font:400 34px var(--serif)", text: "Fresh off the boat" }),
    el("div", { class: "actions" }, el("a", { class: "btn ghost small", href: "#/library" }, "All runs", icon("arrow")))));
  recentWrap.append(stats.recent.length ? el("div", { class: "runs" }, ...stats.recent.map((r, i) => runCard(r, i)))
    : el("div", { class: "empty card" }, icon("net"), el("h3", { text: "The net is empty" }), el("p", { text: "Paste an address above to make your first haul." })));
  return stopNet;
};
