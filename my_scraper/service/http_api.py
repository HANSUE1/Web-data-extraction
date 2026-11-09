"""HTTP API: the same scrape, crawl, map and extract requests, as JSON over HTTP.

Run with `python -m my_scraper.service.http_api` and send, for example:

    curl -X POST http://127.0.0.1:8787/v1/scrape -H "Content-Type: application/json" \\
         -d '{"url": "https://example.com/"}'

It listens on 127.0.0.1 only unless told otherwise. Set SCRAPER_API_KEY (or pass
--api-key) to require "Authorization: Bearer <key>" on every request.
"""
import argparse
import hmac
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web  # noqa: E402

from my_scraper.service.engine import ScrapeService, ServiceError  # noqa: E402

MAX_BODY_BYTES = 64 * 1024


def _accepted(method: Callable) -> List[str]:
    return [name for name in inspect.signature(method).parameters if name != "self"]


def _endpoint(service: ScrapeService, name: str):
    method = getattr(service, name)
    allowed = _accepted(method)

    async def handle(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _error(400, "invalid_json", "The body must be a JSON object.")
        if not isinstance(body, dict):
            return _error(400, "invalid_json", "The body must be a JSON object.")
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


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


def create_app(service: Optional[ScrapeService] = None, api_key: Optional[str] = None) -> web.Application:
    service = service or ScrapeService()

    @web.middleware
    async def require_key(request: web.Request, handler):
        if api_key and request.path != "/health":
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(supplied, api_key):
                return _error(401, "unauthorized", "Send Authorization: Bearer <api key>.")
        return await handler(request)

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

    async def close(_app):
        await service.close()

    app.on_cleanup.append(close)
    return app


ENDPOINTS: Dict[str, str] = {"scrape": "scrape", "crawl": "crawl", "map": "map_site", "extract": "extract"}


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
