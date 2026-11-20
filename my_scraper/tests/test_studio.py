"""The studio app: results browsing, the local-only guard, and jobs run end to end."""
import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from my_scraper.service.engine import ScrapeService
from my_scraper.studio import sketch
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
                {"urls": ["https://a.test"], "ignore_robots_for": ["a.test"]},
                {"urls": ["https://a.test", "--ignore-robots-for=a.test"]},
                {"urls": ["https://a.test"], "include": ["--ignore-robots-for=a.test"]},
                {"urls": ["https://a.test"], "selectors": {"-x": "h1"}}):
        try:
            config_from_request(bad)
        except InvalidJob:
            continue
        raise AssertionError(f"accepted {bad}")
    config = config_from_request({"urls": "https://a.test/", "max_pages": 5, "examples": {"title": "Chair"}, "mode": "static"})
    assert config.max_pages == 5 and config.examples == {"title": "Chair"} and config.allowed_domains == ["a.test"]
    assert config_from_request({"urls": ["https://a.test/"], "fields": ["title", "price", "title"]}).fields == ["title", "price"]


def test_picked_fields_are_all_the_export_keeps(site, tmp_path):
    site.add("/", "Home", links=["/a"])
    site.add("/a", "Page A")

    async def steps(client):
        body = {"urls": [site.url + "/"], "mode": "static", "rate_limit": 1000, "max_pages": 5,
                "selectors": {"title": "h1::text", "links[]": "a::attr(href)"}, "fields": ["title"], "export": ["jsonl"]}
        job_id = (await (await client.post("/api/jobs", json=body, headers=CHANGE)).json())["id"]
        for _ in range(200):
            job = await (await client.get(f"/api/jobs/{job_id}")).json()
            if job["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.05)
        assert job["status"] == "done", job
        with open(job["summary"]["exports"]["jsonl"], encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        assert sorted(row["title"] for row in rows) == ["Home", "Page A"] and all(list(row) == ["title"] for row in rows)

    async def go():
        service = ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)
        async with TestClient(TestServer(create_app("outputs/results", service))) as client:
            await steps(client)
    asyncio.run(go())


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
        drawn = [e for e in job["events"] if e["message"] == "page_sketch"]
        assert len(drawn) == 3 and drawn[0]["sketch"]["blocks"]  # the live view gets every page to draw
        runs = (await (await client.get("/api/runs")).json())["runs"]
        assert [r["id"] for r in runs] == [job["run_id"]] and runs[0]["items"] == 3

    # the crawler writes to ./outputs/results; point the library there too
    async def go():
        service = ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)
        async with TestClient(TestServer(create_app("outputs/results", service))) as client:
            await steps(client)
    asyncio.run(go())


def test_sketch_keeps_text_links_and_marks_items_and_next_links():
    html = """<html><head><title>Chairs</title></head><body>
      <nav><a href="/">Home</a><a href="/about">About</a></nav>
      <ul><li><a href="/c/1">Garden</a></li><li><a href="/c/2">Office</a></li><li><a href="/c/3">Kids</a></li></ul>
      <h1>All chairs</h1>
      <div class="card"><h3><a href="/item/1">Folding garden chair</a></h3><p>Weatherproof, <b>$19</b></p></div>
      <p>More on <a href="/page/2">page 2</a> and <a href="mailto:x@y.z">mail</a>.</p>
      <script>var hidden = "not text";</script></body></html>"""
    queued = {"https://a.test/page/2", "https://a.test/item/1"}
    drawn = sketch.build("https://a.test/", {"content": html, "headers": {"content-type": "text/html"}},
                         [{"title": "Folding garden chair", "price": "$19"}], lambda href: href in queued)
    assert drawn["title"] == "Chairs" and drawn["nav"] == ["Home", "About"]
    kinds = [b["k"] for b in drawn["blocks"]]
    assert kinds[0] == "links" and "Garden" in str(drawn["blocks"][0]["parts"])  # a menu is one cloud of links
    item = next(b for b in drawn["blocks"] if b.get("item"))
    assert item["parts"][0][:3] == ["Folding garden chair", "https://a.test/item/1", True]
    paragraph = drawn["blocks"][-1]
    assert ["page 2", "https://a.test/page/2", True] in paragraph["parts"]
    assert "mail" in "".join(part[0] for part in paragraph["parts"]) and not any("mailto" in str(part[1]) for part in paragraph["parts"])
    assert "not text" not in str(drawn)


def test_sketch_of_data_lists_the_items():
    drawn = sketch.build("https://a.test/api", {"content": "[]", "headers": {"content-type": "application/json"}},
                         [{"name": "Garden chair"}, {"name": "Desk lamp"}], lambda href: False)
    assert [b["parts"][0][0] for b in drawn["blocks"]] == ["Garden chair", "Desk lamp"]



def test_listing_the_library_reads_each_file_once(tmp_path, monkeypatch):
    """Listing used to re-parse every result file on each request, stalling a running crawl."""
    from my_scraper.studio import results
    write_run(tmp_path)
    library = results.Library(str(tmp_path / "results"))
    reads = []
    real = results._read_rows
    monkeypatch.setattr(results, "_read_rows", lambda path: reads.append(path) or real(path))
    library.runs()
    library.runs()
    assert len(reads) == 2  # the run's .jsonl and .csv, once each


def test_page_copies_are_capped_but_events_keep_their_order():
    from my_scraper.studio.jobs import MAX_SKETCHES, Job
    job = Job({}, config_from_request({"urls": ["https://a.test/"]}))
    for n in range(MAX_SKETCHES + 30):
        job.add_event({"message": "http_request", "url": f"https://a.test/{n}"})
        job.add_event({"message": "page_sketch", "url": f"https://a.test/{n}", "sketch": {}})
    events = job.snapshot(since=0)["events"]
    assert sum(e["message"] == "page_sketch" for e in events) == MAX_SKETCHES
    assert sum(e["message"] == "http_request" for e in events) == MAX_SKETCHES + 30
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
