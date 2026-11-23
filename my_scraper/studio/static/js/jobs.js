/* Seine Studio: keeping up with the crawls that run in the background. */

import { $, nf, api, toast } from "./ui.js";
import { state, go, runHash } from "./shell.js";

export async function pollJobs() {
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
