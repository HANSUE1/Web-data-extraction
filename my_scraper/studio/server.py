"""The studio app: the HTTP API plus routes for jobs and results, and the UI that uses them.

    python -m my_scraper.studio            then open http://127.0.0.1:8790

It answers this computer only. There is no login, so two checks keep other websites
open in the same browser from using it: the Host header must name this computer (no DNS
rebinding), and every change needs an X-Studio header, which a cross-site page can't send.
"""
import argparse
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web  # noqa: E402

from my_scraper.config.settings import RESULTS_DIR  # noqa: E402
from my_scraper.service.engine import ScrapeService  # noqa: E402
from my_scraper.service.http_api import _dumps, _error, create_app as create_api  # noqa: E402
from my_scraper.studio.jobs import InvalidJob, JobQueue  # noqa: E402
from my_scraper.studio.results import Library, NotFound  # noqa: E402

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def _int(request: web.Request, name: str, default: int) -> int:
    try:
        return int(request.query.get(name, default))
    except ValueError:
        return default


def create_app(results_dir: str = RESULTS_DIR, service: Optional[ScrapeService] = None) -> web.Application:
    app = create_api(service)
    library = Library(results_dir)
    jobs = JobQueue()

    @web.middleware
    async def local_only(request: web.Request, handler):
        if request.url.host not in LOCAL_HOSTS:
            return _error(403, "not_local", "The studio only answers this computer.")
        if request.method not in SAFE_METHODS and request.headers.get("X-Studio") != "1":
            return _error(403, "missing_header", "Changes need the X-Studio: 1 header.")
        try:
            response = await handler(request)
        except NotFound as e:
            return _error(404, "not_found", f"No such file or folder in the results: {e}")
        if request.path.startswith("/static/") or request.path == "/":
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.middlewares.append(local_only)

    def run_id(path: Optional[str]) -> Optional[str]:
        try:
            return Path(path).resolve().relative_to(library.root).as_posix() if path else None
        except ValueError:
            return None

    def job_view(job, since=None):
        data = job.snapshot(since)
        data["run_id"] = run_id(data["summary"].get("run_dir"))
        return data

    # --- UI -------------------------------------------------------------------

    async def index(_request):
        return web.FileResponse(STATIC / "index.html")

    # --- Results ----------------------------------------------------------------

    async def runs(_request):
        return _json({"root": str(library.root), "runs": library.runs()})

    async def stats(_request):
        return _json(library.stats())

    async def view_file(request):
        return _json(library.view(request.query.get("path", ""), offset=_int(request, "offset", 0),
                                  limit=_int(request, "limit", 100), query=request.query.get("q", ""),
                                  sort=request.query.get("sort") or None,
                                  descending=request.query.get("desc") == "1"))

    async def download(request):
        path = library.resolve(request.query.get("path", ""))
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})

    async def delete_run(request):
        library.delete_run(request.query.get("path", ""))
        return _json({"deleted": request.query.get("path")})

    async def reveal(request):
        """Open a run's folder in the file manager."""
        relative = request.query.get("path", "")
        path = library.resolve(relative, kind="dir") if relative else library.root
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606 - a folder inside the results, checked above
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        return _json({"opened": str(path)})

    # --- Jobs -------------------------------------------------------------------

    async def list_jobs(_request):
        ordered = sorted(jobs.jobs.values(), key=lambda job: job.created, reverse=True)
        return _json({"jobs": [job_view(job) for job in ordered]})

    async def submit_job(request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _error(400, "invalid_json", "The body must be a JSON object.")
        try:
            job = jobs.submit(body)
        except InvalidJob as e:
            return _error(400, "invalid_job", str(e))
        return _json(job_view(job), status=201)

    async def get_job(request):
        job = jobs.jobs.get(request.match_info["id"])
        if not job:
            return _error(404, "not_found", "No such job.")
        return _json(job_view(job, since=_int(request, "since", 0)))

    async def cancel_job(request):
        job = jobs.cancel(request.match_info["id"])
        if not job:
            return _error(404, "not_found", "No such job.")
        return _json(job_view(job))

    app.router.add_get("/", index)
    app.router.add_static("/static", STATIC)
    app.router.add_get("/api/runs", runs)
    app.router.add_get("/api/stats", stats)
    app.router.add_get("/api/file", view_file)
    app.router.add_get("/api/download", download)
    app.router.add_delete("/api/runs", delete_run)
    app.router.add_post("/api/reveal", reveal)
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_post("/api/jobs", submit_job)
    app.router.add_get("/api/jobs/{id}", get_job)
    app.router.add_post("/api/jobs/{id}/cancel", cancel_job)

    async def start_jobs(_app):
        jobs.start()

    async def stop_jobs(_app):
        await jobs.stop()

    app.on_startup.append(start_jobs)
    app.on_shutdown.append(stop_jobs)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Web data extraction studio (local app)")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--no-open", action="store_true", help="Don't open the browser")
    args = parser.parse_args(argv)
    os.chdir(PROJECT_ROOT)  # results, logs and state land in this project's outputs/, as with the CLI
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Studio running at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, [url]).start()  # once the server listens
    web.run_app(create_app(), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
