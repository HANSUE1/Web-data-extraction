/* Seine Studio: what every view shares. App state, addresses, and the run card. */

import { $, reduced, nf, el, icon, api, toast, confirmBox, fmtBytes, ago, mark } from "./ui.js";

export const state = { runs: null, jobs: [], seenStatus: {} };
export async function loadRuns(force = false) {
  if (!state.runs || force) state.runs = (await api("/api/runs")).runs;
  return state.runs;
}

// ---------------------------------------------------------------- addresses
/** Go to a view. Going to the one already open loads it afresh. */
export const go = (hash) => { if (location.hash === hash) window.dispatchEvent(new Event("hashchange")); else location.hash = hash; };
export const runHash = (id, file) => `#/run/${encodeURIComponent(id)}` + (file ? `?file=${encodeURIComponent(file)}` : "");
export function crumbs(...parts) {
  $("#crumbs").replaceChildren(...parts.flatMap((p, i) => [i ? el("span", { text: "/" }) : null, i === parts.length - 1 ? el("b", { text: p }) : el("span", { text: p })]).filter(Boolean));
}
export const openFolder = (path) => api("/api/reveal", { method: "POST", query: { path } }).catch((e) => toast(e.message, true));

// ---------------------------------------------------------------- run card
export function runCard(run, index = 0) {
  const formats = [...new Set(run.files.map((f) => f.format))];
  const reveal = el("button", { class: "icon-btn", title: "Open folder", "aria-label": "Open folder", onclick: (e) => { e.stopPropagation(); openFolder(run.id); } }, icon("folder"));
  const del = el("button", { class: "icon-btn", title: "Delete run", "aria-label": "Delete run", onclick: async (e) => { e.stopPropagation(); await deleteRun(run, card); } }, icon("trash"));
  const when = new Date(run.created * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const card = el("article", { class: "run", tabindex: 0, style: `animation-delay:${Math.min(index, 14) * 40}ms`,
    onclick: () => go(runHash(run.id)), onkeydown: (e) => { if (e.key === "Enter" && e.target === card) go(runHash(run.id)); } },  // not for Enter on its buttons
    el("div", { class: "top" }, mark(run.site), el("div", {}, el("div", { class: "site", text: run.site }), el("div", { class: "when", title: run.name, text: `${ago(run.created)} · ${when}` }))),
    el("div", { class: "count" + (run.items ? "" : " none"), text: run.items == null ? "—" : nf.format(run.items) }, el("small", { text: run.items === 1 ? "item" : "items" })),
    el("div", { class: "foot" }, ...formats.map((f) => el("span", { class: "fmt " + f, text: f })), el("span", { class: "size", text: fmtBytes(run.size) })),
    el("div", { class: "acts" }, reveal, run.id ? del : null));  // loose files in the results folder are not a run to delete
  card.addEventListener("pointermove", (e) => {
    const r = card.getBoundingClientRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    card.style.setProperty("--mx", px * 100 + "%"); card.style.setProperty("--my", py * 100 + "%");
    if (!reduced && !card.parentElement?.classList.contains("list")) card.style.transform = `perspective(900px) rotateX(${(.5 - py) * 5}deg) rotateY(${(px - .5) * 6}deg) translateY(-3px)`;
  });
  card.addEventListener("pointerleave", () => { card.style.transform = ""; });
  return card;
}

export async function deleteRun(run, node) {
  if (!(await confirmBox("Delete this run?", `${run.name} — ${run.files.length} file(s), ${fmtBytes(run.size)}. The files are removed from disk.`))) return false;
  try {
    await api("/api/runs", { method: "DELETE", query: { path: run.id } });
    state.runs = (state.runs || []).filter((r) => r.id !== run.id);
    if (node) { node.style.transition = "all .4s cubic-bezier(.22,1,.36,1)"; node.style.opacity = 0; node.style.transform = "scale(.9)"; setTimeout(() => node.remove(), 400); }
    toast("Run deleted");
    return true;
  } catch (err) { toast(err.message, true); return false; }
}
