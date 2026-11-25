"""How often a page is worth reading again, from how often it has changed."""
from statistics import median
from typing import Dict, List

from my_scraper.storage.snapshots import SnapshotStore

SHORTEST = 3600.0          # seconds
LONGEST = 30 * 86400.0
UNKNOWN = 86400.0          # nothing known yet: once a day


def recrawl_interval(store: SnapshotStore, url: str) -> float:
    """Half the usual time between changes, so most changes are seen soon. Pages that never change are left longer."""
    history = store.history(url)
    if len(history) < 2:
        return UNKNOWN
    changes = [history[0]["fetched_at"]] + [after["fetched_at"] for before, after in zip(history, history[1:], strict=False)
                                            if after["content_hash"] != before["content_hash"]]
    if len(changes) < 2:
        return min(max((history[-1]["fetched_at"] - history[0]["fetched_at"]) * 2, UNKNOWN), LONGEST)
    gaps = [after - before for before, after in zip(changes, changes[1:], strict=False)]
    return min(max(median(gaps) / 2, SHORTEST), LONGEST)


def recrawl_plan(store: SnapshotStore, limit: int = 100) -> List[Dict[str, object]]:
    """The pages most overdue for another read."""
    plan = []
    for snapshot in store.find(latest=True):
        interval = recrawl_interval(store, snapshot["url"])
        plan.append({"url": snapshot["url"], "every_hours": round(interval / 3600, 1),
                     "due_at": snapshot["fetched_at"] + interval})
    return sorted(plan, key=lambda entry: entry["due_at"])[:limit]
