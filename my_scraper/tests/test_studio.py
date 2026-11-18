"""The studio app: results browsing, the local-only guard, and jobs run end to end."""
import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from my_scraper.service.engine import ScrapeService
from my_scraper.studio.jobs import InvalidJob, config_from_request
from my_scraper.studio.server import create_app

CHANGE = {"X-Studio": "1"}


def studio(tmp_path, steps):
    """Run `steps(client)` against a fresh studio whose results live in tmp_path/results."""
    async def go():
        service = ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)
        async with TestClient(TestServer(create_app(str(tmp_path / "results"), service))) as client:
            return await steps(client)
    return asyncio.run(go())


def write_run(tmp_path, name="shop.test_20261118_120000"):
    run = tmp_path / "results" / name
    run.mkdir(parents=True)
    rows = [{"title": f"Chair {n}", "price": str(n * 10)} for n in (1, 2, 10)]
    (run / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (run / f"{name}.csv").write_text("title,price\nChair 1,10\nChair 2,20\nChair 10,100\n", encoding="utf-8")
    return name


def test_lists_runs_and_reads_rows(tmp_path):
    name = write_run(tmp_path)

    async def steps(client):
        runs = (await (await client.get("/api/runs")).json())["runs"]
        assert [(r["id"], r["site"], r["items"]) for r in runs] == [(name, "shop.test", 3)]
        path = f"{name}/{name}.jsonl"
        view = await (await client.get("/api/file", params={"path": path, "sort": "price", "desc": "1"})).json()
        assert [r["price"] for r in view["rows"]] == ["100", "20", "10"]  # by number, not as text
        found = await (await client.get("/api/file", params={"path": path, "q": "chair 2"})).json()
        assert found["matched"] == 1 and found["total"] == 3
        stats = await (await client.get("/api/stats")).json()
        assert stats["items"] == 3 and stats["sites"] == 1

    studio(tmp_path, steps)


def test_paths_outside_the_results_are_refused(tmp_path):
    write_run(tmp_path)
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")

    async def steps(client):
        for path in ("../secret.txt", str(tmp_path / "secret.txt"), ""):
            assert (await client.get("/api/file", params={"path": path})).status == 404
            assert (await client.get("/api/download", params={"path": path})).status == 404
        assert (await client.delete("/api/runs", params={"path": ".."}, headers=CHANGE)).status == 404
        assert (await client.delete("/api/runs", params={"path": ""}, headers=CHANGE)).status == 404

    studio(tmp_path, steps)
    assert (tmp_path / "secret.txt").exists() and (tmp_path / "results").exists()


def test_changes_need_the_studio_header_and_a_local_host(tmp_path):
    name = write_run(tmp_path)

    async def steps(client):
        assert (await client.delete("/api/runs", params={"path": name})).status == 403
        assert (await client.get("/api/runs", headers={"Host": "evil.example"})).status == 403
        assert (await client.delete("/api/runs", params={"path": name}, headers=CHANGE)).status == 200

    studio(tmp_path, steps)
    assert not (tmp_path / "results" / name).exists()


def test_job_requests_are_validated_like_the_cli():
    for bad in ({}, {"urls": ["ftp://x"]}, {"urls": ["https://a.test"], "max_pages": "9"},
                {"urls": ["https://a.test"], "mode": "turbo"}, {"urls": ["https://a.test"], "login": ["u", "p"]},
                {"urls": ["https://a.test"], "ignore_robots_for": ["a.test"]}):
        try:
            config_from_request(bad)
        except InvalidJob:
            continue
        raise AssertionError(f"accepted {bad}")
    config = config_from_request({"urls": "https://a.test/", "max_pages": 5, "examples": {"title": "Chair"}, "mode": "static"})
    assert config.max_pages == 5 and config.examples == {"title": "Chair"} and config.allowed_domains == ["a.test"]


def test_a_job_crawls_and_its_run_shows_in_the_library(site, tmp_path):
    site.add("/", "Home", links=["/a", "/b"])
    site.add("/a", "Page A")
    site.add("/b", "Page B")

    async def steps(client):
        body = {"urls": [site.url + "/"], "mode": "static", "rate_limit": 1000, "max_pages": 5,
                "selectors": {"title": "h1::text"}, "export": ["jsonl"]}
        created = await client.post("/api/jobs", json=body, headers=CHANGE)
        assert created.status == 201
        job_id = (await created.json())["id"]
        for _ in range(200):
            job = await (await client.get(f"/api/jobs/{job_id}", params={"since": 0})).json()
            if job["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.05)
        assert job["status"] == "done", job
        assert job["progress"]["pages"] == 3 and job["progress"]["items"] == 3
        assert any(e["message"] == "crawl_completed" for e in job["events"])
        runs = (await (await client.get("/api/runs")).json())["runs"]
        assert [r["id"] for r in runs] == [job["run_id"]] and runs[0]["items"] == 3

    # the crawler writes to ./outputs/results; point the library there too
    async def go():
        service = ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)
        async with TestClient(TestServer(create_app("outputs/results", service))) as client:
            await steps(client)
    asyncio.run(go())
