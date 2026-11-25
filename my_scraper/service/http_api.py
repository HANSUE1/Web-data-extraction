"""HTTP API: the same scrape, crawl, map and extract requests, as JSON over HTTP.

Run with `python -m my_scraper.service.http_api` and send, for example:

    curl -X POST http://127.0.0.1:8787/v1/scrape -H "Content-Type: application/json" \\
         -d '{"url": "https://example.com/"}'

It listens on 127.0.0.1 only unless told otherwise. Set SCRAPER_API_KEY (or pass
--api-key) to require "Authorization: Bearer <key>" on every request.
"""
import argparse
import asyncio
import inspect
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web  # noqa: E402

from my_scraper.jobqueue.store import STATES, Job, open_queue  # noqa: E402
from my_scraper.ops.dashboard import add_routes, authorized, unauthorized  # noqa: E402
from my_scraper.service.engine import ScrapeService, ServiceError  # noqa: E402

MAX_BODY_BYTES = 64 * 1024


async def _json_body(request: web.Request) -> Dict[str, Any]:
    """The request's JSON object. Raises ValueError with the reason when it has none.

    The content type must say JSON: a web page can post plain text to this computer without the
    browser asking first, but not JSON.
    """
    if request.content_type != "application/json":
        raise ValueError("Send Content-Type: application/json.")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise ValueError("The body must be a JSON object.") from None
    if not isinstance(body, dict):
        raise ValueError("The body must be a JSON object.")
    return body


def _accepted(method: Callable) -> List[str]:
    return [name for name in inspect.signature(method).parameters if name != "self"]


def _endpoint(service: ScrapeService, name: str):
    method = getattr(service, name)
    allowed = _accepted(method)

    async def handle(request: web.Request) -> web.Response:
        try:
            body = await _json_body(request)
        except ValueError as e:
            return _error(400, "invalid_json", str(e))
        unknown = sorted(set(body) - set(allowed))
        if unknown:
            return _error(400, "unknown_fields", f"Unknown fields {unknown}. Accepted: {allowed}")
        try:
            return web.json_response(await method(**body), dumps=_dumps)
        except ServiceError as e:
            return web.json_response(e.to_dict(), status=e.status)
        except TypeError as e:  # a missing or wrongly typed field
            return _error(400, "invalid_request", str(e))

    return handle


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(entry) for entry in value]
    return value


def _dumps(data: Any) -> str:
    """JSON that any client can read: Python would write NaN and Infinity, which are not JSON."""
    try:
        return json.dumps(data, ensure_ascii=False, default=str, allow_nan=False)
    except ValueError:
        return json.dumps(_finite(data), ensure_ascii=False, default=str)


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


def _job_view(job: Job) -> Dict[str, Any]:
    return {"id": job.id, "state": job.state, "attempts": job.attempts, "max_attempts": job.max_attempts,
            "created": job.created, "finished": job.finished, "error": job.error, "result": job.result}


def _add_jobs(app: web.Application, queue: Callable[[], Any]):
    """Crawls too long for one request: queued here, run by `python -m my_scraper.ops worker`."""
    async def submit(request: web.Request) -> web.Response:
        from my_scraper.studio.jobs import InvalidJob, config_from_request

        try:
            body = await _json_body(request)
        except ValueError as e:
            return _error(400, "invalid_json", str(e))
        try:
            config_from_request(body)  # the settings a request may choose, which exclude every safety rule
        except InvalidJob as e:
            return _error(400, "invalid_job", str(e))
        key = json.dumps(body, sort_keys=True)
        job_id = await asyncio.to_thread(lambda: queue().enqueue({"request": body}, dedupe_key=key))
        return web.json_response({"id": job_id, "state": "queued"}, status=202)

    async def show(request: web.Request) -> web.Response:
        job = await asyncio.to_thread(lambda: queue().get(request.match_info["id"]))
        return web.json_response(_job_view(job), dumps=_dumps) if job else _error(404, "not_found", "No such job.")

    async def list_jobs(request: web.Request) -> web.Response:
        state = request.query.get("state")
        if state is not None and state not in STATES:
            return _error(400, "invalid_request", f"state must be one of {', '.join(STATES)}")
        jobs = await asyncio.to_thread(lambda: queue().list(state))
        return web.json_response({"jobs": [_job_view(job) for job in jobs]}, dumps=_dumps)

    async def cancel(request: web.Request) -> web.Response:
        done = await asyncio.to_thread(lambda: queue().cancel(request.match_info["id"]))
        return web.json_response({"cancelled": True}) if done else _error(409, "not_cancellable", "Not waiting or running.")

    app.router.add_post("/v1/jobs", submit)
    app.router.add_get("/v1/jobs", list_jobs)
    app.router.add_get("/v1/jobs/{id}", show)
    app.router.add_post("/v1/jobs/{id}/cancel", cancel)


def create_app(service: Optional[ScrapeService] = None, api_key: Optional[str] = None,
               queue=None) -> web.Application:
    service = service or ScrapeService()
    opened = [queue] if queue is not None else []

    def the_queue():
        if not opened:
            opened.append(open_queue())
        return opened[0]

    @web.middleware
    async def require_key(request: web.Request, handler):
        return await handler(request) if authorized(request, api_key) else unauthorized(request)

    app = web.Application(middlewares=[require_key], client_max_size=MAX_BODY_BYTES)

    async def health(_request):
        return web.json_response({"status": "ok"})

    async def describe(_request):
        return web.json_response({name: _accepted(getattr(service, method))
                                  for name, method in ENDPOINTS.items()})

    app.router.add_get("/health", health)
    app.router.add_get("/v1", describe)
    for name, method in ENDPOINTS.items():
        app.router.add_post(f"/v1/{name}", _endpoint(service, method))
    _add_jobs(app, the_queue)
    add_routes(app, the_queue)

    async def close(_app):
        await service.close()

    app.on_cleanup.append(close)
    return app


ENDPOINTS: Dict[str, str] = {"scrape": "scrape", "crawl": "crawl", "map": "map_site", "extract": "extract",
                             "watch": "watch", "interact": "interact"}


def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="HTTP API for web-data-extraction")
    parser.add_argument("--host", default="127.0.0.1", help="Address to listen on (default: this computer only)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--api-key", default=os.environ.get("SCRAPER_API_KEY"),
                        help="Require this key as a Bearer token (default: $SCRAPER_API_KEY)")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.api_key:
        parser.error("listening beyond this computer needs --api-key, or anyone could use it")
    web.run_app(create_app(api_key=args.api_key), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
