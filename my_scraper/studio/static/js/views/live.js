/* Live: a crawl as it runs, with the spider, the silk map and the event feed. */

import { $, $$, nf, el, icon, api, toast, clock, seg } from "../ui.js";
import { state, crumbs, go, runHash } from "../shell.js";
import { pollJobs } from "../jobs.js";
import { SilkMap } from "../radar.js";
import { pageStatus, PageWalk } from "../spider.js";

// outcomes that are nobody's refusal or failure
const FINE = ["success", "duplicate_content", "unchanged", "no_data_extracted"];
const FEED_ROWS = 500;

export const live = async (root, parts, ctx) => {
  crumbs("Seine", "Live");
  root.append(el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "The net, ", el("em", { text: "live" })),
    el("p", { text: "Watch the spider wrap every item it takes and tap the links it follows next. The silk map beside it draws the site as a web, with a pulse for every page fetched." })),
    el("div", { class: "actions" }, el("a", { class: "btn primary", href: "#/extract" }, icon("bolt"), "New extraction"))));
  const listBox = el("div", { class: "jobs" });
  const monitor = el("div", { class: "monitor" });
  root.append(el("div", { class: "live" }, el("div", { class: "live-left" }, listBox), monitor));

  let selected = parts[0] || null, timer = null, net = null, spider = null, since = 0, feedEl = null, gauges = null, lastStatus = null;

  let drawn = "";
  const progressText = (j) => `${j.progress.pages}p · ${nf.format(j.progress.items)} items`;
  function drawList() {
    const jobs = state.jobs, ids = jobs.map((j) => j.id).join();
    if (jobs.length && ids === drawn) {  // the same crawls: updated in place, so a click or the keyboard focus is never lost
      jobs.forEach((j, i) => {
        const node = listBox.children[i], status = $(".state", node);
        status.className = "state " + j.status; status.textContent = j.status;
        status.nextSibling.textContent = progressText(j);
      });
      return;
    }
    drawn = ids;
    if (!jobs.length) {
      listBox.replaceChildren(el("div", { class: "card empty", style: "padding:30px 16px" }, el("p", { text: "No crawls in this session yet." })));
      return;
    }
    const first = !listBox.classList.contains("settled");
    listBox.classList.add("settled");
    listBox.replaceChildren(...jobs.map((j, i) => el("button", { class: "job" + (j.id === selected ? " on" : ""), style: first ? `animation-delay:${i * 40}ms` : "animation:none", onclick: () => { if (j.id !== selected) go(`#/live/${j.id}`); } },
      el("div", { class: "u", text: j.urls.map((u) => u.replace(/^https?:\/\//, "")).join(", ") }),
      el("div", { class: "m" }, el("span", { class: "state " + j.status, text: j.status }), el("span", { text: progressText(j) })))));
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
    const bad = Object.entries(p.statuses).filter(([s]) => !FINE.includes(s)).reduce((s, [, n]) => s + n, 0);
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
    const frag = document.createDocumentFragment(), firstRow = events.length - FEED_ROWS;  // a long crawl replays thousands
    for (const [n, ev] of events.entries()) {
      if (ev.message === "page_sketch") { spider.sketch(ev.url, ev.sketch); continue; }
      if (ev.url) {
        const status = pageStatus(ev);
        if (ev.message !== "trying_browser" || !net.nodes.has(net.key(ev.url))) net.add(ev.url, status);
        if (ev.message === "http_request" || status === "refused" || status === "error") spider.visit(ev.url, status);
        if (ev.message === "parsing_complete") spider.collect(ev.url, ev.items_extracted || 0);
      }
      if (n < firstRow) continue;
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
    while (feedEl.children.length > FEED_ROWS) feedEl.firstChild.remove();
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
      if (err.status !== 404) { timer = setTimeout(tick, 2500); return; }  // no answer is not the same as no such crawl
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
