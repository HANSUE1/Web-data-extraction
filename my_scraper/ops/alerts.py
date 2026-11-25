"""Tell the owner when something needs a person: a webhook (Slack, Teams, anything that takes JSON) and a log file."""
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from my_scraper.ops.history import History
from my_scraper.utils.http import create_session
from my_scraper.utils.timeutil import utc_now_iso

MIN_PAGES = 10           # a success rate over fewer pages says nothing
REPEAT_AFTER = 3600      # seconds before the same rule alerts again
ATTEMPTS = 3


@dataclass
class Alert:
    rule: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


async def post_json(url: str, data: Dict[str, Any]) -> bool:
    """Deliver to a webhook. A receiver that is down is retried, and never stops the crawl."""
    body = json.dumps(data, default=str)
    for attempt in range(ATTEMPTS):
        try:
            async with create_session({"Content-Type": "application/json"}, aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(url, data=body) as response:
                    if response.status < 500:
                        return response.status < 400
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        await asyncio.sleep(0.5 * 2 ** attempt)
    return False


class Notifier:
    def __init__(self, history: History, log_file: str, webhook: Optional[str] = None):
        self.history, self.webhook = history, webhook
        self.log_file = Path(log_file)

    async def send(self, alert: Alert, repeat_after: float = REPEAT_AFTER) -> bool:
        """Raise an alert, unless the same rule fired recently. Returns whether it was raised."""
        if time.time() - self.history.last_alert(alert.rule) < repeat_after:
            return False
        entry = {"timestamp": utc_now_iso(), "rule": alert.rule, "message": alert.message, **alert.details}
        self.history.add_alert(alert.rule, alert.message)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        if self.webhook:
            await post_json(self.webhook, {"text": f"[{alert.rule}] {alert.message}", **entry})
        return True


def window_alerts(history: History, min_success_ratio: float, minutes: int) -> List[Alert]:
    counts = history.window(minutes)
    total = counts["ok"] + counts["failed"]
    if total < MIN_PAGES or counts["ok"] / total >= min_success_ratio:
        return []
    return [Alert("success_rate", f"{counts['ok'] / total:.0%} of {total} pages succeeded in the last {minutes} "
                                  f"minutes, below {min_success_ratio:.0%}", {"window": counts})]


def queue_alerts(counts: Dict[str, int], max_depth: int) -> List[Alert]:
    alerts = []
    if counts.get("queued", 0) > max_depth:
        alerts.append(Alert("queue_depth", f"{counts['queued']} jobs are waiting, more than {max_depth}"))
    if counts.get("dead", 0):
        alerts.append(Alert("dead_letters", f"{counts['dead']} jobs used up their attempts and need a person"))
    return alerts
