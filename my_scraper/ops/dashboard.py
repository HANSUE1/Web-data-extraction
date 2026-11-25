"""Live operations page, its data as JSON, and /metrics for Prometheus."""
import asyncio
import base64
import binascii
import hmac
import time
from html import escape
from typing import Any, Dict, List, Optional

from aiohttp import web

from my_scraper.config.settings import STATE_DIR
from my_scraper.ops.history import History
from my_scraper.ops.metrics import METRICS

REFRESH_SECONDS = 5
WINDOW_MINUTES = 15

STYLE = """
:root{color-scheme:light dark;--line:color-mix(in srgb,currentColor 16%,transparent);--bad:#c0392b;--good:#1e8449}
@media(prefers-color-scheme:dark){:root{--bad:#ff8a7a;--good:#7fd99a}}
body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}
h1{font-size:1.4rem;margin:0}h2{font-size:1rem;margin:2rem 0 .5rem}
.tiles{display:flex;flex-wrap:wrap;gap:2rem;margin:1.5rem 0}.tiles dt{font-size:.85rem;opacity:.75}
.tiles dd{margin:0;font-size:1.8rem;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:.35rem .75rem .35rem 0;border-bottom:1px solid var(--line);vertical-align:top}
td{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.bad{color:var(--bad)}.good{color:var(--good)}
"""


def status(queue, state_dir: str) -> Dict[str, Any]:
    history = History(state_dir)
    window = history.window(WINDOW_MINUTES)
    total = window["ok"] + window["failed"]
    return {"queue": queue.counts(), "workers": queue.workers(), "window_minutes": WINDOW_MINUTES, "window": window,
            "success_ratio": round(window["ok"] / total, 4) if total else None, "runs": history.runs(15),
            "errors": history.errors(15), "alerts": history.alerts(10)}


def _when(moment: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment)) if moment else "-"


def _table(headings: List[str], rows: List[List[Any]], empty: str) -> str:
    if not rows:
        return f"<p>{escape(empty)}</p>"
    head = "".join(f'<th scope="col">{escape(heading)}</th>' for heading in headings)
    body = "".join("<tr>" + "".join(f"<td>{escape(str('-' if cell is None else cell))}</td>" for cell in row) + "</tr>"
                   for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _share(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0%}"


def render(data: Dict[str, Any]) -> str:
    queue, ratio = data["queue"], data["success_ratio"]
    tone = "" if ratio is None else ' class="good"' if ratio >= 0.9 else ' class="bad"'
    tiles = [("Waiting jobs", queue["queued"], ""), ("Running", queue["running"], ""),
             ("Dead letters", queue["dead"], ' class="bad"' if queue["dead"] else ""),
             ("Busy workers", len(data["workers"]), ""),
             (f"Success, last {data['window_minutes']} min", _share(ratio), tone)]
    shown = "".join(f"<div><dt>{escape(name)}</dt><dd{css}>{escape(str(value))}</dd></div>" for name, value, css in tiles)
    runs = [[_when(run["started"]), run["site"], run["state"], run["pages"], run["items"], _share(run["success_ratio"]),
             _share((run["quality"] or {}).get("valid_ratio")), "; ".join(run["notes"] or [])] for run in data["runs"]]
    errors = [[_when(e["at"]), e["status"], e["url"], e["error"]] for e in data["errors"]]
    alerts = [[_when(a["at"]), a["rule"], a["message"]] for a in data["alerts"]]
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}"><title>Scraper operations</title>'
            f"<style>{STYLE}</style></head><body><h1>Scraper operations</h1><dl class=\"tiles\">{shown}</dl>"
            f"<h2>Alerts</h2>{_table(['When', 'Rule', 'What'], alerts, 'No alerts.')}"
            f"<h2>Runs</h2>{_table(['Started', 'Site', 'State', 'Pages', 'Items', 'Success', 'Valid', 'Notes'], runs, 'No runs yet.')}"
            f"<h2>Recent errors</h2>{_table(['When', 'Status', 'Page', 'Error'], errors, 'No errors.')}</body></html>")


def add_routes(app: web.Application, open_queue, state_dir: str = STATE_DIR):
    """/dashboard, /v1/status and /metrics. open_queue is called per request, so a queue is only made when asked for."""
    def read() -> Dict[str, Any]:
        return status(open_queue(), state_dir)

    async def dashboard(_request):
        return web.Response(text=render(await asyncio.to_thread(read)), content_type="text/html",
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    async def as_json(_request):
        return web.json_response(await asyncio.to_thread(read))

    async def metrics(_request):
        for state, count in (await asyncio.to_thread(lambda: open_queue().counts())).items():
            METRICS.set("scraper_jobs", count, state=state)
        return web.Response(text=METRICS.render(), content_type="text/plain", charset="utf-8",
                            headers={"X-Content-Type-Options": "nosniff"})

    app.router.add_get("/dashboard", dashboard)
    app.router.add_get("/v1/status", as_json)
    app.router.add_get("/metrics", metrics)


READ_PAGES = {"/dashboard", "/metrics", "/v1/status"}


def authorized(request: web.Request, api_key: Optional[str]) -> bool:
    """Whether a request carries the key: as a Bearer token or, for pages that only show things, as a Basic password.

    A browser can only ask for a password. It also sends one along with requests other sites make,
    so a password is never enough to start anything.
    """
    if not api_key or request.path == "/health":
        return True
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "basic" and request.method == "GET" and request.path in READ_PAGES:
        try:
            supplied = base64.b64decode(supplied, validate=True).decode("utf-8", "replace").partition(":")[2]
        except (binascii.Error, ValueError):
            return False
    elif scheme.lower() != "bearer":
        return False
    # As bytes: compare_digest refuses text that is not ASCII, and the header can hold anything.
    return hmac.compare_digest(supplied.strip().encode("utf-8", "surrogateescape"), api_key.encode("utf-8"))


def unauthorized(request: web.Request) -> web.Response:
    headers = {"WWW-Authenticate": 'Basic realm="scraper"'} if request.path in READ_PAGES else {}
    return web.json_response({"error": "unauthorized", "message": "Send Authorization: Bearer <api key>."}, status=401,
                             headers=headers)


def create_app(open_queue, state_dir: str = STATE_DIR, api_key: Optional[str] = None) -> web.Application:
    @web.middleware
    async def require_key(request: web.Request, handler):
        return await handler(request) if authorized(request, api_key) else unauthorized(request)

    app = web.Application(middlewares=[require_key])

    async def alive(_request):
        return web.json_response({"status": "ok"})

    app.router.add_get("/health", alive)
    add_routes(app, open_queue, state_dir)
    return app

