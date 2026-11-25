"""What running in production adds: snapshots and replay, data quality, compliance rules, proxies, monitoring, delivery."""
import asyncio
import base64
import json
import os
import sqlite3
import subprocess
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from parsel import Selector

from my_scraper.cli.main import build_config, build_parser
from my_scraper.compliance import risk
from my_scraper.compliance.manager import ComplianceManager, country_of
from my_scraper.config.secret_refs import SecretMissing, resolve
from my_scraper.config.settings import CrawlConfig, LLMConfig
from my_scraper.core import crawler as crawler_module
from my_scraper.core.crawler import NotCleared, ProductionCrawler
from my_scraper.core.links import robots_directives
from my_scraper.core.replay import ReplayError, replay
from my_scraper.fetcher import actions
from my_scraper.fetcher.proxies import NoProxyAvailable, ProxyPool, for_browser, label
from my_scraper.jobqueue.schedule import load_schedules
from my_scraper.jobqueue.store import SQLiteQueue
from my_scraper.ops import __main__ as ops
from my_scraper.ops import dashboard, gate
from my_scraper.ops.alerts import Alert, Notifier, queue_alerts, window_alerts
from my_scraper.ops.health import health, verdict
from my_scraper.ops.history import History, success_ratio
from my_scraper.ops.metrics import Metrics
from my_scraper.parser.llm import LLMExtractor, grounded, shape_of
from my_scraper.parser.selectors import SelectorCache, values
from my_scraper.parser.universal_parser import HTMLParser
from my_scraper.quality.dedup import Deduplicator, distance, simhash
from my_scraper.quality.normalize import normalize_item, parse_date, parse_price
from my_scraper.quality.provenance import PageProvenance
from my_scraper.quality.report import SchemaValidator, drift, quality_of, threshold_failures
from my_scraper.search.index import SearchIndex, hits, pagerank
from my_scraper.search.recrawl import LONGEST, SHORTEST, UNKNOWN, recrawl_interval
from my_scraper.service.engine import ScrapeService, ServiceError
from my_scraper.service.http_api import create_app
from my_scraper.storage.blobs import DamagedBlob, LocalBlobs, S3Blobs
from my_scraper.storage.database import DatabaseError, deliver
from my_scraper.storage.snapshots import SnapshotStore

PRODUCT_SCHEMA = {"type": "object", "required": ["title", "price"], "properties": {
    "title": {"type": "string", "minLength": 1}, "price": {"type": "string", "pattern": "\\d"}}}


def listing(*products):
    cards = "".join(f'<article class="p"><h3><a href="/p/{slug}">{title}</a></h3><p class="price">{price}</p></article>'
                    for title, price, slug in products)
    return f"<html><body><main>{cards}</main></body></html>"


PRODUCTS = [(f"Kettle model {letter}", f"£{n}.00", f"k{n}") for n, letter in enumerate("ABCDE", 1)]


def basic(password):
    return {"Authorization": "Basic " + base64.b64encode(f"any:{password}".encode()).decode()}


def crawl(config, **options):
    crawler = ProductionCrawler(config)
    asyncio.run(crawler.crawl(**options))
    return crawler


@pytest.fixture
def auto_config(make_config):
    """A crawl of one listing page, with items found automatically."""
    def build(**overrides):
        config = make_config(**{"max_depth": 0, **overrides})
        config.selectors = {}
        config.alerts.enabled = False
        return config
    return build


# --- Snapshots and replay ------------------------------------------------------------


def test_every_fetch_is_kept_with_what_was_parsed_from_it(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    crawler = crawl(auto_config())
    [snapshot] = crawler.snapshots.find()
    assert (snapshot["run_id"], snapshot["status"], snapshot["fetcher"]) == (crawler.run_id, 200, "static")
    assert crawler.snapshots.fetch_result(snapshot)["content"] == listing(*PRODUCTS)
    extraction = crawler.snapshots.extraction(snapshot["id"])
    assert len(extraction["items"]) == 5 and extraction["parser_version"].startswith("1.")


def test_snapshots_cannot_be_changed_and_damage_is_noticed(tmp_path):
    store = SnapshotStore(str(tmp_path))
    snapshot_id = store.save("https://x.test/", {"content": "hello", "headers": {"set-cookie": "s=1", "etag": "e"}})
    with pytest.raises(sqlite3.IntegrityError), sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE snapshots SET url = 'https://evil.test/'")
    db.close()
    [snapshot] = store.find(snapshot_id=snapshot_id)
    assert "set-cookie" not in snapshot["headers"] and "etag" in snapshot["headers"]
    path = next((tmp_path / "blobs").rglob("*.gz"))
    path.write_bytes(path.read_bytes()[:-9] + b"tampered!")
    with pytest.raises(DamagedBlob):
        store.fetch_result(snapshot)


def test_binary_bodies_and_rendered_states_survive_the_round_trip(tmp_path):
    store = SnapshotStore(str(tmp_path))
    result = {"content": "", "body": b"%PDF-\x00\xff", "headers": {"content-type": "application/pdf"},
              "snapshots": ["<p>tab</p>"], "json_payloads": [{"a": 1}], "status_code": 200, "fetcher_type": "browser"}
    store.save("https://x.test/a.pdf", result)
    back = store.fetch_result(store.find()[0])
    assert {name: back[name] for name in ("body", "snapshots", "json_payloads")} == \
        {name: result[name] for name in ("body", "snapshots", "json_payloads")}


def test_prune_deletes_old_snapshots_and_only_their_blobs(tmp_path):
    store = SnapshotStore(str(tmp_path))
    old = store.save("https://x.test/", {"content": "shared", "status_code": 200})
    with sqlite3.connect(store.db_path) as db:  # age it, the way only a test may
        db.executescript("DROP TRIGGER snapshots_immutable; UPDATE snapshots SET fetched_at = 0;")
    db.close()
    store.save("https://x.test/", {"content": "shared", "status_code": 200})
    assert store.prune(30) == 1 and not store.find(snapshot_id=old)
    assert store.fetch_result(store.find()[0])["content"] == "shared"


def test_s3_blobs_round_trip():
    class Missing(Exception):
        pass

    class Bucket:
        exceptions = SimpleNamespace(NoSuchKey=Missing)

        def __init__(self):
            self.objects = {}

        def put_object(self, Bucket, Key, Body):
            self.objects[Bucket, Key] = Body

        def get_object(self, Bucket, Key):
            if (Bucket, Key) not in self.objects:
                raise Missing
            return {"Body": SimpleNamespace(read=lambda: self.objects[Bucket, Key])}

    client = Bucket()
    blobs = S3Blobs("s3://archive/raw", client)
    digest = blobs.put(b"page")
    assert blobs.get(digest) == b"page" and blobs.get("0" * 64) is None
    assert list(client.objects) == [("archive", f"raw/{digest[:2]}/{digest}.gz")]


def test_a_blob_is_stored_once(tmp_path):
    blobs = LocalBlobs(str(tmp_path))
    assert blobs.put(b"same") == blobs.put(b"same")
    assert len(list(tmp_path.rglob("*.gz"))) == 1 and not list(tmp_path.rglob("*.tmp"))


def test_replay_finds_what_a_parser_change_did(site, auto_config, monkeypatch):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    crawler = crawl(auto_config())
    hits_before = sum(site.hits.values())
    report, _ = replay(crawler.snapshots, crawler.snapshots.find())
    assert (report.replayed, report.same, report.changed) == (1, 1, [])

    from my_scraper.parser import universal_parser

    real = universal_parser.extract_items
    monkeypatch.setattr(universal_parser, "extract_items",
                        lambda *args: [{k: v for k, v in item.items() if k != "price"} for item in real(*args)])
    report, records = replay(crawler.snapshots, crawler.snapshots.find())
    assert report.changed[0]["changed"] == 5 and len(records) == 5
    assert sum(site.hits.values()) == hits_before  # the site was not asked again


def test_replay_with_an_unknown_revision_says_so(tmp_path):
    store = SnapshotStore(str(tmp_path))
    store.save("https://x.test/", {"content": "<p>x</p>", "status_code": 200})
    with pytest.raises(ReplayError):
        replay(store, store.find(), revision="no-such-revision")


def test_replay_runs_an_older_parser(tmp_path):
    root = Path(__file__).resolve().parents[2]
    if subprocess.run(["git", "cat-file", "-e", "HEAD:my_scraper/parser/universal_parser.py"], cwd=root,
                      capture_output=True, check=False).returncode:
        pytest.skip("not a git checkout")
    store = SnapshotStore(str(tmp_path))
    store.save("https://x.test/", {"content": listing(*PRODUCTS), "status_code": 200,
                                   "headers": {"content-type": "text/html"}})
    report, records = replay(store, store.find(), revision="HEAD")
    assert report.recovered == 1 and [record["title"] for record in records] == [title for title, _, _ in PRODUCTS]


def test_a_parse_failure_is_kept_apart_from_a_fetch_failure(site, auto_config, capsys):
    site.add_raw("/", listing(*PRODUCTS), "text/html")

    def broken(*_args):
        raise RuntimeError("layout changed")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("my_scraper.parser.universal_parser.UniversalParser.parse_page", broken)
        crawler = crawl(auto_config())
    assert crawler.compliance.status_counts == {"parse_failed": 1} and not crawler._refusals
    [entry] = crawler.retry_manager.dlq.get_all()
    assert entry["stage"] == "parse" and crawler.snapshots.find(snapshot_id=entry["snapshot_id"])
    assert site.hits["/"] == 1

    capsys.readouterr()
    assert ops.main(["dlq", "retry", "--queue", "q.db"]) == 0  # the parser works again: parsed from the snapshot
    assert json.loads(capsys.readouterr().out)["recovered"] == 1
    assert len(crawler.snapshots.extraction(entry["snapshot_id"])["items"]) == 5
    assert site.hits["/"] == 1 and crawler.retry_manager.dlq.get_all() == []
    assert len(list(Path("outputs/state").glob("dead_letter_queue_*.jsonl"))) == 1  # set aside, not deleted


# --- Robots tags ------------------------------------------------------------------------


@pytest.mark.parametrize("meta, header, expected", [
    ("", "", set()),
    ('<meta name="robots" content="noindex, follow">', "", {"noindex", "follow"}),
    ('<META NAME="ROBOTS" CONTENT="NONE">', "", {"none", "noindex", "nofollow"}),
    ("", "noindex", {"noindex"}),
    ("", "otherbot: noindex", set()),
    ("", "ScraperBot: noarchive", {"noarchive"}),
    ('<meta name="scraperbot" content="noindex">', "", {"noindex"}),
    ('<meta name="otherbot" content="noindex">', "", set()),
])
def test_robots_tags(meta, header, expected):
    assert robots_directives(f"<html><head>{meta}</head></html>", {"x-robots-tag": header}, "ScraperBot") == expected


def test_a_noindex_page_is_not_kept_but_its_links_are_followed(site, make_config):
    filler = "<p>" + "words " * 100 + "</p>"
    site.add_raw("/", f'<html><head><meta name="robots" content="noindex"></head><body><h1>Hub</h1>{filler}'
                      '<a href="/a">a</a></body></html>', "text/html")
    site.add("/a", "A")
    crawler = crawl(make_config())
    assert [item["title"] for item in crawler.results] == ["A"]
    assert crawler.compliance.status_counts["robots_noindex"] == 1
    assert [row["url"] for row in crawler.snapshots.find()] == [site.url + "/a"]
    assert not crawler.raw_storage.path_for(site.url + "/").exists()

    config = make_config()
    config.robots.respect_meta = False
    assert {item["title"] for item in crawl(config).results} == {"Hub", "A"}


# --- Data quality --------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("£51.77", (51.77, "GBP")), ("1.234,56 €", (1234.56, "EUR")), ("$1,234.56", (1234.56, "USD")),
    ("1,234", (1234.0, None)), ("12,5", (12.5, None)), ("US$ 9", (9.0, "USD")), ("A$10", (10.0, "AUD")),
    ("CHF 1'299.90", (1299.9, "CHF")), ("0,999", (0.999, None)), ("1 299 zł", (1299.0, "PLN")),
    ("EUR 1.299", (1299.0, "EUR")), ("1.234.567", (1234567.0, None)), ("free", (None, None)), (19, (19.0, None)),
])
def test_prices(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("2026-11-26", "2026-11-26"), ("2026-11-26T10:00:00Z", "2026-11-26T10:00:00+00:00"),
    ("26 November 2026", "2026-11-26"), ("Nov 26, 2026", "2026-11-26"), ("03/04/2026", None), ("soon", None),
    ("2026-13-45", None),
])
def test_only_certain_dates_are_read(text, expected):
    assert parse_date(text) == expected


def test_normalizing_keeps_the_original_and_converts_by_the_given_rate():
    item = normalize_item({"price": "£10.00", "date": "Nov 26, 2026", "brand": "N/A", "n": 3}, "USD", {"GBP": 1.25})
    assert item == {"price": "£10.00", "date": "Nov 26, 2026", "brand": None, "n": 3, "price_amount": 10.0,
                    "price_currency": "GBP", "price_usd": 12.5, "date_iso": "2026-11-26"}
    assert "price_usd" not in normalize_item({"price": "€10"}, "USD", {})  # no rate, no guess


def test_records_that_break_the_schema_are_set_aside_with_the_reason(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS, ("Broken kettle", "ask us", "k9")), "text/html")
    config = auto_config()
    config.quality.schema = PRODUCT_SCHEMA
    crawler = crawl(config)
    exported = [json.loads(line) for line in Path(crawler.exports["jsonl"]).read_text(encoding="utf-8").splitlines()]
    assert len(exported) == 5 and crawler.quality["valid_ratio"] == pytest.approx(5 / 6, abs=0.001)
    [rejected] = Path(crawler.storage.last_run_dir, "rejected.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(rejected)["record"]["title"] == "Broken kettle" and "price" in json.loads(rejected)["errors"][0]


def test_a_dataset_below_its_thresholds_is_quarantined_and_not_delivered(site, auto_config, tmp_path):
    site.add_raw("/", listing(*PRODUCTS[:2]), "text/html")
    config = auto_config()
    config.quality.min_items = 5
    config.storage.database_url = f"sqlite:///{tmp_path / 'out.db'}"
    crawler = crawl(config)
    assert Path(crawler.exports["jsonl"]).parts[-3] == "quarantine" and crawler.exports["database"] is None
    [run] = crawler.history.runs()
    assert run["state"] == "quarantined" and "at least 5 expected" in run["notes"][0]


def test_a_schema_that_cannot_be_applied_costs_no_records(site, auto_config, monkeypatch):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = auto_config()
    config.quality.schema = PRODUCT_SCHEMA

    def broken(*_args):
        raise RecursionError("schema refers to itself")

    monkeypatch.setattr(SchemaValidator, "split", broken)
    crawler = crawl(config)
    assert Path(crawler.exports["jsonl"]).stat().st_size and "RecursionError" in crawler.storage.errors["validation"]


def test_thresholds_and_drift():
    quality = quality_of([{"a": 1, "b": None}, {"a": 2, "b": 3}], rejected=2, pages=4, required=["a", "b"])
    assert quality["valid_ratio"] == 0.5 and quality["null_required_ratio"] == 0.25 and quality["fill"] == {"a": 1.0, "b": 0.5}
    assert len(threshold_failures(quality, 3, 0.9, 0.1)) == 3 and threshold_failures(quality, 2, 0.5, 0.25) == []
    usual = {"items_per_page": 4.0, "fill": {"a": 1.0, "b": 1.0}}
    assert drift(quality, [usual] * 2) == []  # too little history to say
    assert [line.split()[0] for line in drift(quality, [usual] * 3)] == ["items", "field"]


def test_extraction_drift_raises_an_alert(site, auto_config):
    for prices in (True, True, True, False):
        site.add_raw("/", listing(*[(title, price if prices else "", slug) for title, price, slug in PRODUCTS]), "text/html")
        config = auto_config()
        config.alerts.enabled = True
        crawler = crawl(config)
    [alert] = crawler.history.alerts()
    assert alert["rule"].startswith("drift:") and "'price'" in alert["message"]
    assert Path("outputs/logs/alerts.jsonl").exists()


def test_a_bad_schema_is_refused_before_crawling():
    with pytest.raises(Exception, match="nonsense"):
        SchemaValidator({"type": "nonsense"})


def test_simhash_is_close_for_near_copies_only():
    words = [f"word{n}" for n in range(300)]
    other = [f"term{n}" for n in range(300)]
    near = words[:150] + ["edited"] + words[151:]
    assert distance(simhash(" ".join(words)), simhash(" ".join(near))) <= 6
    assert distance(simhash(" ".join(words)), simhash(" ".join(other))) > 15


def test_deduplication_within_and_across_runs(tmp_path):
    article = "The quick brown fox jumps over the lazy dog near the river bank today. " * 12
    path = str(tmp_path / "dedup.db")
    run = Deduplicator(path, across_runs=True)
    assert run.duplicate_of({"url": "/a", "title": "A", "price": "1"}) is None
    assert run.duplicate_of({"url": "/b", "title": "A", "price": "1"}) is None  # another page, another product
    assert run.duplicate_of({"url": "/a", "title": "A", "price": "1", "source": "table"}) == "/a"
    assert run.duplicate_of({"url": "/one", "text": article}) is None
    assert run.duplicate_of({"url": "/two", "text": article, "author": "Someone"}) == "/one"

    crashed = Deduplicator(path, across_runs=True)  # the first run was never published
    assert crashed.duplicate_of({"url": "/a", "title": "A", "price": "1"}) is None
    crashed.commit()
    later = Deduplicator(path, across_runs=True)
    assert later.duplicate_of({"url": "/a", "title": "A", "price": "1"}) == "/a" and later.repeats_earlier_run
    assert later.duplicate_of({"url": "/a", "title": "A", "price": "2"}) is None  # the price changed
    assert Deduplicator(path, across_runs=False).duplicate_of({"url": "/a", "title": "A", "price": "1"}) is None


def test_cross_run_dedupe_still_follows_pages_and_reports_no_removals(site, auto_config):
    def publish():
        first = listing(*PRODUCTS[:3]).replace("</main>", '</main><a rel="next" href="/?page=2">next</a>')
        site.add_raw("/", first, "text/html")
        site.add_raw("/?page=2", listing(*PRODUCTS[3:], *extra), "text/html")
        config = auto_config(track_changes=True)
        config.quality.dedupe = "cross"
        config.quality.min_items = 5
        return crawl(config)

    extra = [("Kettle model F", "£6.00", "k6")]
    assert len(publish().results) == 6
    extra.append(("Kettle model G", "£7.00", "k7"))
    second = publish()
    assert [item["title"] for item in second.results] == ["Kettle model G"]
    assert (len(second.changes.new), second.changes.removed, second.storage.quarantined) == (1, [], False)


def test_provenance_points_at_the_exact_bytes(site, make_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = make_config(max_depth=0, item_selector=".p")
    config.selectors = {"name": "h3 a::text", "price": ".price::text"}
    config.quality.provenance = True
    crawler = crawl(config)
    body = crawler.snapshots.fetch_result(crawler.snapshots.find()[0])["content"].encode()
    record = crawler.results[1]
    origin = record["_meta"]["fields"]["name"]
    assert body[slice(*origin["bytes"])].decode() == record["name"] == "Kettle model B"
    assert origin["occurrences"] == 1 and PageProvenance(
        {"url": "u", "content": "a 3 b 3"}, {}, None, "p", "s", "r").of({"rating": "3"})["fields"]["rating"]["bytes"] is None
    assert origin["selector"] == ".p >> h3 a::text" and origin["snapshot_id"] == record["_meta"]["snapshot_id"]
    assert {"parser_version", "schema_version", "run_id"} <= set(record["_meta"])


def test_the_manifest_describes_the_dataset(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    crawler = crawl(auto_config())
    [path] = Path("outputs/state/manifests").glob("*.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == crawler.run_id and manifest["quality"]["items"] == 5
    [(name, described)] = manifest["files"].items()
    import hashlib

    assert hashlib.sha256(Path(manifest["folder"], name).read_bytes()).hexdigest() == described["sha256"]


# --- Delivery to a database -------------------------------------------------------------------


DATABASES = ["sqlite"] + (["postgres"] if os.environ.get("SCRAPER_TEST_POSTGRES") else [])


@pytest.fixture(params=DATABASES)
def database(request, tmp_path):
    """(url, query) for a database with an empty table."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'out.db'}"

        def query(sql):
            db = sqlite3.connect(tmp_path / "out.db")
            try:
                return db.execute(sql).fetchall()
            finally:
                db.close()
        return url, query
    import psycopg

    url = os.environ["SCRAPER_TEST_POSTGRES"]

    def query(sql):
        with psycopg.connect(url) as db:
            return db.execute(sql).fetchall()
    with psycopg.connect(url) as db:
        db.execute("DROP TABLE IF EXISTS records")
    return url, query


def test_upsert_keeps_one_row_per_record(database):
    url, query = database
    item = {"url": "https://x.test/a", "title": "A", "price": "1", "note": "naïve – 日本 \x00"}
    deliver([item], url, "records", "upsert", ["url", "title"], "crawl", "run_1")
    deliver([{**item, "price": "2"}], url, "records", "upsert", ["url", "title"], "crawl", "run_2")
    [(data, run, first, last)] = query("SELECT data, run_id, first_seen, last_seen FROM records")
    assert json.loads(data)["price"] == "2" and json.loads(data)["note"] == item["note"]
    assert run == "run_2" and first <= last


def test_append_and_snapshot_delivery(database):
    url, query = database
    for run in ("run_1", "run_2"):
        deliver([{"url": "/a"}], url, "records", "append", ["url"], "crawl", run)
    assert query("SELECT count(*) FROM records") == [(2,)]
    deliver([{"url": "/b"}], url, "records", "snapshot", ["url"], "crawl", "run_3")
    deliver([{"url": "/c"}], url, "records", "snapshot", ["url"], "another", "run_4")
    assert sorted(json.loads(row[0])["url"] for row in query("SELECT data FROM records")) == ["/b", "/c"]


def test_rows_sharing_a_key_are_separate_records(database):
    url, query = database
    rows = [{"url": "/t", "name": name} for name in "abc"]
    assert deliver(rows, url, "records", "upsert", ["url"], "crawl", "run_1") == 3
    assert query("SELECT count(*) FROM records") == [(3,)]


@pytest.mark.parametrize("url, table", [("mysql://x", "records"), ("sqlite:///x.db", "records; DROP TABLE x")])
def test_a_database_that_cannot_be_used_is_refused(url, table):
    with pytest.raises(DatabaseError):
        deliver([{"url": "/a"}], url, table, "upsert", ["url"], "crawl", "run")


def test_an_interrupted_run_does_not_replace_a_delivered_snapshot(site, auto_config, tmp_path):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = auto_config()
    config.storage.database_url, config.storage.delivery = f"sqlite:///{tmp_path / 'out.db'}", "snapshot"
    crawler = ProductionCrawler(config)
    crawler.storage.complete = False
    crawler.storage.export([{"url": "/a"}])
    assert "interrupted" in crawler.storage.errors["database"] and not (tmp_path / "out.db").exists()


SUNK = []


def sink(items, _config):
    SUNK.extend(items)


def test_your_own_sink_gets_the_records(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = auto_config()
    config.storage.sinks = [f"{__name__}:sink", "no.such.module:x"]
    crawler = crawl(config)
    assert len(SUNK) == 5 and "no.such.module:x" in crawler.storage.errors and crawler.exports["jsonl"]


# --- Compliance -----------------------------------------------------------------------------------


def test_redaction_takes_out_personal_data_and_leaves_the_rest():
    redacted = ComplianceManager.redact(ComplianceManager.__new__(ComplianceManager), {
        "url": "https://x.test/2026-555-1234/a", "title": "Contact jane.doe@example.com or +1 415 555 2671",
        "phone": "anything", "tags": ["a", "ssn 123-45-6789"], "nested": {"email": "x@y.co"}, "price": "£51.77", "n": 5})
    assert redacted == {"url": "https://x.test/2026-555-1234/a", "title": "Contact [redacted] or [redacted]",
                        "phone": "[redacted]", "tags": ["a", "ssn [redacted]"], "nested": {"email": "[redacted]"},
                        "price": "£51.77", "n": 5}


def test_redacting_long_text_is_quick():
    started = time.perf_counter()
    ComplianceManager.redact_text("a" * 200_000 + " " + "1234567890" * 5_000)
    assert time.perf_counter() - started < 5


def test_redaction_happens_before_records_are_stored(site, make_config):
    site.add_raw("/", "<html><body><h1>Write to jane@example.com</h1>" + "<p>words</p>" * 60 + "</body></html>", "text/html")
    config = make_config(max_depth=0)
    config.compliance.redact_pii = True
    crawler = crawl(config)
    assert crawler.results[0]["title"] == "Write to [redacted]"
    assert "jane@" not in crawler.state.items_file.read_text(encoding="utf-8")
    assert "title" in crawler.compliance.personal_data_found  # still flagged


@pytest.mark.parametrize("host, country", [("a.de", "de"), ("bbc.co.uk", "gb"), ("x.com", None), ("127.0.0.1", None), ("b.eu", "eu")])
def test_country_of(host, country):
    assert country_of(host) == country


def test_country_rules(make_config):
    config = make_config()
    config.compliance.denied_countries = ["eu", "uk"]
    manager = ComplianceManager(config)
    for url in ("https://a.de/", "https://a.co.uk/"):
        with pytest.raises(ValueError, match="denied_countries"):
            manager.validate_url(url)
    assert manager.validate_url("https://a.com/") and manager.validate_url("https://a.jp/")
    config.compliance.denied_countries, config.compliance.allowed_countries = [], ["jp"]
    with pytest.raises(ValueError, match="allowed_countries"):
        ComplianceManager(config).validate_url("https://a.com/")


REVIEWED = {"tos_verdict": "allows", "tos_reviewed": "2026-11-01"}


@pytest.mark.parametrize("entry, facts, verdict_expected", [
    (REVIEWED, {}, risk.GO),
    (None, {}, risk.REVIEW),
    ({**REVIEWED, "tos_verdict": "prohibits"}, {}, risk.NO_GO),
    ({**REVIEWED, "tos_reviewed": "2024-01-01"}, {}, risk.REVIEW),
    ({"tos_verdict": "allows"}, {}, risk.REVIEW),
    (REVIEWED, {"robots_allowed": False}, risk.NO_GO),
    (REVIEWED, {"blocked": "on the takedown list"}, risk.NO_GO),
    (REVIEWED, {"page_status": "blocked_by_site"}, risk.NO_GO),
    (REVIEWED, {"authenticated": True}, risk.REVIEW),
    ({**REVIEWED, "login_permitted": True}, {"authenticated": True}, risk.GO),
    (REVIEWED, {"api_candidates": ["https://x.test/api"]}, risk.REVIEW),
    ({**REVIEWED, "api_rejected_because": "incomplete"}, {"api_candidates": ["https://x.test/api"]}, risk.GO),
    (REVIEWED, {"personal_data": ["email"]}, risk.REVIEW),
    ({**REVIEWED, "legal_basis": "LIA-12"}, {"personal_data": ["email"]}, risk.GO),
    ({**REVIEWED, "data_class": "sensitive"}, {}, risk.NO_GO),
])
def test_go_or_no_go(entry, facts, verdict_expected):
    decision = risk.decide(risk.Facts("x.test", entry, **facts), today=date(2026, 11, 26))
    assert decision.verdict == verdict_expected, decision.reasons


def test_a_crawl_that_needs_clearance_does_not_start_without_it(site, auto_config, tmp_path):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = auto_config()
    config.compliance.require_go = True
    with pytest.raises(NotCleared, match="not in the register"):
        crawl(config)
    assert not site.hits

    register = tmp_path / "sites.json"
    register.write_text(json.dumps({"127.0.0.1": {**REVIEWED, "tos_reviewed": date.today().isoformat()}}), encoding="utf-8")
    config.compliance.site_register = str(register)
    assert len(crawl(config).results) == 5


def test_a_register_covers_subdomains_and_a_broken_one_is_refused(tmp_path):
    entry = risk.register_decision("https://shop.example.com/", {"example.com": REVIEWED}).facts["entry"]
    assert entry == REVIEWED
    path = tmp_path / "r.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(risk.RegisterError):
        risk.load_register(str(path))
    assert risk.known_api("https://en.wikipedia.org/wiki/X")


def test_assess_looks_at_the_site_itself(site, auto_config):
    site.add_raw("/", '<html><body><h1>Shop</h1><a href="/terms">Terms of use</a><a href="/api/docs">API</a>'
                      + "<p>words here</p>" * 60 + "</body></html>", "text/html")
    decision = asyncio.run(risk.assess(auto_config(), site.url + "/"))
    assert decision.verdict == risk.REVIEW
    assert decision.facts["terms_links"] == [site.url + "/terms"] and site.url + "/api/docs" in decision.facts["api_candidates"]

    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /", "text/plain")
    assert asyncio.run(risk.assess(auto_config(), site.url + "/")).verdict == risk.NO_GO


# --- Secrets and configuration ----------------------------------------------------------------------


def test_secrets_are_read_from_where_the_setting_points(tmp_path, monkeypatch):
    monkeypatch.setenv("A_SECRET", "s3cret")
    (tmp_path / "f").write_text("from file\n", encoding="utf-8")
    assert resolve("env:A_SECRET") == "s3cret" and resolve(f"file:{tmp_path / 'f'}") == "from file"
    assert resolve("plain") == "plain" and resolve(None) is None
    for missing in ("env:NOT_SET_ANYWHERE", "file:/no/such/file"):
        with pytest.raises(SecretMissing):
            resolve(missing)
    config = CrawlConfig.from_dict({"proxy": {"urls": ["env:A_SECRET"]}, "storage": {"database_url": "env:A_SECRET"},
                                    "login": {"url": "https://x.test/", "username": "me", "password": "env:A_SECRET"}})
    assert (config.proxy.urls, config.storage.database_url, config.login["password"]) == (["s3cret"], "s3cret", "s3cret")


def test_allow_noindex_is_a_choice_made_on_the_command_line():
    parser = build_parser()
    assert build_config(parser.parse_args(["--urls", "https://x.test/"]), parser).robots.respect_meta
    args = parser.parse_args(["--urls", "https://x.test/", "--allow-noindex"])
    assert not build_config(args, parser).robots.respect_meta


@pytest.mark.parametrize("raw", [{"storage": {"delivery": "sometimes"}}, {"quality": {"dedupe": "maybe"}},
                                 {"llm": {"enabled": True}}])
def test_settings_that_name_no_choice_are_refused(raw):
    with pytest.raises(ValueError):
        CrawlConfig.from_dict(raw)


def test_the_example_configuration_is_usable(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SCRAPER_DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("SCRAPER_ALERT_WEBHOOK", "https://hooks.test/")
    config = CrawlConfig.from_dict(json.loads((root / "config/production.example.json").read_text(encoding="utf-8")))
    SchemaValidator(config.quality.schema)
    register = risk.load_register(str(root / "config/sites.example.json"))
    assert risk.register_decision(config.start_urls[0], register).verdict in (risk.GO, risk.REVIEW)
    assert len(load_schedules(str(root / "config/schedules.example.json"))) == 2


def test_a_password_alone_cannot_start_anything(site, service, tmp_path):
    async def steps():
        app = create_app(service, api_key="key", queue=SQLiteQueue(str(tmp_path / "q.db")))
        async with TestClient(TestServer(app)) as client:
            body = {"urls": [site.url + "/"]}
            assert (await client.post("/v1/jobs", json=body, headers=basic("key"))).status == 401
            assert (await client.post("/v1/jobs", json=body, headers={"Authorization": "Bearer key"})).status == 202

    asyncio.run(steps())


# --- Proxies ------------------------------------------------------------------------------------------


def test_a_site_keeps_its_proxy_and_moves_only_when_it_fails():
    pool = ProxyPool([f"http://user:pw@proxy{n}.test:8080" for n in range(4)], max_failures=2, rest_seconds=60)
    sites = [f"https://site{n}.test/page" for n in range(40)]
    before = {url: pool.for_url(url) for url in sites}
    assert len(set(before.values())) > 1 and all(pool.for_url(url) == before[url] for url in sites)
    broken = before[sites[0]]
    pool.failed(broken)
    assert pool.for_url(sites[0]) == broken  # one failure is not a trend
    pool.failed(broken)
    after = {url: pool.for_url(url) for url in sites}
    assert broken not in after.values()
    assert all(after[url] == before[url] for url in sites if before[url] != broken)


def test_when_every_proxy_is_down_nothing_goes_out_directly():
    pool = ProxyPool(["http://p.test:1"], max_failures=1)
    pool.failed("http://p.test:1")
    with pytest.raises(NoProxyAvailable):
        pool.for_url("https://x.test/")


def test_proxy_settings():
    assert ProxyPool([]).for_url("https://x.test/") is None
    assert label("http://user:secret@p.test:8080") == "p.test:8080"
    assert for_browser("http://us%40er:s%3Aecret@p.test:8080") == {
        "server": "http://p.test:8080", "username": "us@er", "password": "s:ecret"}
    with pytest.raises(ValueError):
        ProxyPool(["socks5://p.test:1"])


def test_pages_and_robots_txt_go_out_through_the_proxy_and_a_dead_one_is_replaced(site, other_site, auto_config):
    # An HTTP proxy receives the whole address. The test site stands in for one and answers itself.
    other_site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = auto_config()
    config.proxy.urls, config.proxy.max_failures = ["http://127.0.0.1:9", other_site.url], 1
    config.retry.max_retries = 3
    crawler = crawl(config)
    assert len(crawler.results) == 5 and other_site.hits["/"] == 1 and not site.hits


def test_a_refusal_is_not_answered_by_changing_proxy(site, other_site, auto_config):
    other_site.add("/", status=403)
    config = auto_config()
    config.proxy.urls = [other_site.url]
    crawler = crawl(config)
    assert crawler.fetcher.proxies.healthy() == [other_site.url] and other_site.hits["/"] == 1


def test_proxy_health_check(other_site):
    pool = ProxyPool([other_site.url, "http://127.0.0.1:9"])
    results = asyncio.run(pool.check("http://anything.test/"))
    assert list(results.values()) == [True, False] and pool.healthy() == [other_site.url]


# --- Circuit breaker -----------------------------------------------------------------------------------


def test_the_breaker_opens_on_a_high_error_rate(make_config):
    config = make_config()
    config.concurrency.breaker_window, config.concurrency.breaker_error_rate = 10, 0.6
    crawler = ProductionCrawler(config)
    url = "https://x.test/p"
    for n in range(10):  # never enough refusals in a row
        crawler._note_outcome(url, ok=n % 3 == 2)
    assert crawler._gave_up_on(url) and not crawler._gave_up_on("https://y.test/")


# --- Selectors -------------------------------------------------------------------------------------------


PAGE = Selector(text="""<html><body><h1>Kettle <b>One</b></h1><button aria-label="Add">+</button>
<label for="q">Quantity</label><input id="q" value="3"><dl><dt>Weight</dt><dd>1.2 kg</dd></dl>
<table><tr><th>Colour</th><td>Red</td></tr></table><div role="heading">Reviews</div><a href="/n">Next page</a>
<a href="/b">Buy</a><p>SKU: AB-12 and SKU: CD-34</p><script>SKU: XX-99</script></body></html>""", type="html")


@pytest.mark.parametrize("selector, expected", [
    ("role=heading", ["Kettle One", "Reviews"]), ("role=link[next]", ["Next page"]), ("label=Quantity", ["3"]),
    ("label=Weight", ["1.2 kg"]), ("label=Colour", ["Red"]), ("role=button[Add]", ["+"]), ("label=It's", []),
    (r"text=/SKU: (\S+)/", ["AB-12", "CD-34"]), ("h1 b::text", ["One"]), ("//dd/text()", ["1.2 kg"]),
])
def test_selectors_by_meaning(selector, expected):
    assert values(PAGE, selector) == expected


def test_the_first_alternative_with_a_value_wins():
    assert HTMLParser.first_value(PAGE, ".gone::text || label=Weight || h1::text") == "1.2 kg"
    assert HTMLParser.first_value(PAGE, ".gone::text || .also-gone::text") is None
    page = HTMLParser().parse_rows("<ul><li><b>a</b></li><li><b>b</b></li></ul>", "u", ".gone || role=listitem", {"t[]": "i::text || b::text"})
    assert page.items == [{"t": ["a"]}, {"t": ["b"]}]


def test_what_was_learned_outlives_the_example(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    first = crawl(auto_config(examples={"title": "Kettle model A", "price": "£1.00"}))
    site.add_raw("/", listing(*[(title, "£99.00", slug) for title, _, slug in PRODUCTS]), "text/html")
    second = crawl(auto_config(examples={"title": "Kettle model A", "price": "£1.00"}))
    assert first.learned_selectors == second.learned_selectors
    assert [item["price"] for item in second.results] == ["£99.00"] * 5


def test_the_selector_cache_notices_drift(tmp_path):
    cache = SelectorCache(str(tmp_path / "s.json"))
    assert cache.put("https://x.test/", {"a": 1}, ".row", {"a": "b::text"}) is False
    assert cache.put("https://www.x.test/p", {"a": 2}, ".row", {"a": "b::text"}) is False
    assert cache.put("https://x.test/", {"a": 1}, ".card", {"a": "b::text"}) is True
    assert cache.get("https://x.test/", {"a": 9}) == (".card", {"a": "b::text"})


# --- The language model fallback -----------------------------------------------------------------------------


class FakeModel:
    """Answers like the API, and keeps what it was asked."""

    def __init__(self, records, stop_reason="end_turn"):
        self.asked = []
        self.records, self.stop_reason = records, stop_reason
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **request):
        self.asked.append(request)
        return SimpleNamespace(stop_reason=self.stop_reason, model=request["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps({"records": self.records}))],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def llm(tmp_path, client, **settings):
    return LLMExtractor(LLMConfig(enabled=True, **settings), str(tmp_path / "audit.jsonl"), ComplianceManager.redact_text, client)


def test_the_model_answers_within_the_schema_and_the_page(tmp_path):
    client = FakeModel([{"title": "Copper Kettle", "price": "£45.00"}, {"title": "Invented Teapot", "price": None}])
    extractor = llm(tmp_path, client)
    html = "<html><body><p>Copper Kettle</p><p>£45.00</p><p>call jane@example.com</p></body></html>"
    records = asyncio.run(extractor.extract("https://x.test/", html, PRODUCT_SCHEMA, "snap_1"))
    assert [(r["title"], r["price"]) for r in records] == [("Copper Kettle", "£45.00"), (None, None)]
    [request] = client.asked
    assert request["model"] == "claude-opus-5-5" and "jane@" not in request["messages"][0]["content"]
    schema = request["output_config"]["format"]["schema"]
    assert schema["properties"]["records"]["items"]["additionalProperties"] is False
    assert "pattern" not in json.dumps(schema)  # limits on values are checked here, not by the API
    [audit] = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit["snapshot_id"] == "snap_1" and audit["outcome"] == "ok" and audit["model"] == "claude-opus-5-5"


@pytest.mark.parametrize("client, settings, reason", [
    (FakeModel([], stop_reason="refusal"), {}, "refusal"), (FakeModel([], stop_reason="max_tokens"), {}, "max_tokens"),
    (FakeModel([]), {"max_calls": 0}, "budget"), (FakeModel([]), {"max_chars": 3}, "characters"),
])
def test_the_model_is_left_out_when_it_should_be(tmp_path, client, settings, reason):
    extractor = llm(tmp_path, client, **settings)
    assert asyncio.run(extractor.extract("u", "<body><p>some text</p></body>", PRODUCT_SCHEMA, None)) == []
    assert reason in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_grounding_and_shape():
    assert grounded({"a": "Red", "b": 1234, "c": ["red", "blue"], "d": True}, "Red kettle, 1,234 sold") == {
        "a": "Red", "b": 1234, "c": ["red"], "d": True}
    assert shape_of({"type": "array", "minItems": 1, "items": {"type": "object", "properties": {}}}) == {
        "type": "array", "items": {"type": "object", "properties": {}, "additionalProperties": False}}


def test_the_model_is_only_asked_when_nothing_else_worked(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    config = auto_config()
    config.llm.enabled, config.quality.schema = True, PRODUCT_SCHEMA
    crawler = ProductionCrawler(config)
    crawler.llm._client = client = FakeModel([{"title": "x", "price": "1"}])
    asyncio.run(crawler.crawl())
    assert client.asked == [] and len(crawler.results) == 5

    site.add_raw("/", "<html><body><p>Kettle £1</p></body></html>", "text/html")
    config = auto_config()
    config.llm.enabled, config.quality.schema = True, PRODUCT_SCHEMA
    config.item_selector, config.selectors = ".gone", {"title": "b::text"}  # the layout changed under the selectors
    crawler = ProductionCrawler(config)
    crawler.llm._client = client = FakeModel([{"title": "Kettle", "price": "£1"}])
    asyncio.run(crawler.crawl())
    assert len(client.asked) == 1 and crawler.results[0]["source"] == "llm" and crawler.results[0]["title"] == "Kettle"
    report, _ = replay(crawler.snapshots, crawler.snapshots.find(latest=True))
    assert report.skipped == 1


# --- Browser actions -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("steps", [
    "click", [{}], [{"click": ""}], [{"click": "a", "fill": "b"}], [{"fill": "#q"}], [{"scroll": 999}], [{"scroll": True}],
    [{"press": "F12"}], [{"click": "a", "times": 0}], [{"click": "a"}] * 21, [{"run": "alert(1)"}],
])
def test_bad_actions_are_refused(steps):
    with pytest.raises(actions.ActionError):
        actions.check(steps)


@pytest.mark.parametrize("text, refused", [
    ("Buy now", True), ("Sign up", True), ("Delete", True), ("Post comment", True), ("Load more", False),
    ("Show results", False), ("Next", False), ("Postcode lookup", False), ("Apply filter", False),
])
def test_buttons_that_change_something_are_not_pressed(text, refused):
    assert bool(actions.REFUSED_LABEL.search(text)) is refused


# --- Monitoring -------------------------------------------------------------------------------------------------------


def test_metrics_are_valid_prometheus_text():
    metrics = Metrics()
    metrics.inc("pages", status='a"b\\c\nd')
    metrics.inc("pages", 2, status="ok")
    metrics.set("depth", 1234567890)
    metrics.observe("seconds", 0.2, stage="fetch")
    metrics.observe("seconds", 99, stage="fetch")
    text = metrics.render()
    assert 'pages{status="a\\"b\\\\c\\nd"} 1\n' in text and 'pages{status="ok"} 2\n' in text and "depth 1234567890\n" in text
    assert 'seconds_bucket{stage="fetch",le="0.1"} 0\n' in text and 'seconds_bucket{stage="fetch",le="0.25"} 1\n' in text
    assert 'seconds_bucket{stage="fetch",le="+Inf"} 2\n' in text and 'seconds_count{stage="fetch"} 2\n' in text


def test_success_counts_neither_for_nor_against_pages_the_rules_kept_us_from():
    assert success_ratio({"success": 8, "http_error": 2, "robots_disallowed": 50}) == 0.8
    assert success_ratio({"robots_disallowed": 5}) is None


def test_the_success_rate_alert_goes_out_once(tmp_path):
    history = History(str(tmp_path))
    for n in range(20):
        history.outcome("run", "https://x.test/", "success" if n < 10 else "http_error", "HTTP 500")
    [alert] = window_alerts(history, 0.9, 15)
    assert "50% of 20 pages" in alert.message and window_alerts(history, 0.4, 15) == []
    received = []

    async def steps():
        async def hook(request):
            received.append(await request.json())
            return web.Response(status=503 if len(received) == 1 else 200)

        app = web.Application()
        app.router.add_post("/", hook)
        async with TestServer(app) as server:
            notifier = Notifier(history, str(tmp_path / "alerts.jsonl"), str(server.make_url("/")))
            return [await notifier.send(alert), await notifier.send(alert)]

    assert asyncio.run(steps()) == [True, False]
    assert len(received) == 2 and received[0] == received[1]  # the receiver was down once, so it was sent again
    assert received[0]["text"].startswith("[success_rate]")


def test_a_webhook_that_is_down_does_not_stop_anything(tmp_path):
    notifier = Notifier(History(str(tmp_path)), str(tmp_path / "a.jsonl"), "http://127.0.0.1:9/")
    assert asyncio.run(notifier.send(Alert("r", "m"))) is True


def test_queue_alerts():
    assert [a.rule for a in queue_alerts({"queued": 11, "dead": 1}, 10)] == ["queue_depth", "dead_letters"]
    assert queue_alerts({"queued": 10, "dead": 0}, 10) == []


def test_log_lines_carry_run_job_snapshot_and_stage(site, auto_config):
    site.add_raw("/", listing(*PRODUCTS), "text/html")
    crawler = crawl(auto_config(job_id="job_7"))
    lines = [json.loads(line) for line in Path("outputs/logs/scraper.log").read_text(encoding="utf-8").splitlines()]
    assert all(line["run_id"] == crawler.run_id and line["job_id"] == "job_7" for line in lines)
    [parsed] = [line for line in lines if line["message"] == "parsing_complete"]
    assert parsed["stage"] == "store" and parsed["snapshot_id"] == crawler.snapshots.find()[0]["id"]
    assert "scraper_pages_total" in Path("outputs/logs/metrics.prom").read_text(encoding="utf-8")


def test_the_dashboard_shows_state_and_escapes_it(tmp_path):
    history = History(str(tmp_path))
    history.outcome("run", "https://x.test/<script>alert(1)</script>", "failed", "boom")
    queue = SQLiteQueue(str(tmp_path / "q.db"))
    queue.enqueue({})

    async def steps():
        app = dashboard.create_app(lambda: queue, str(tmp_path), api_key="key")
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/dashboard")).status == 401
            assert (await client.get("/health")).status == 200
            assert (await client.get("/dashboard")).headers["WWW-Authenticate"].startswith("Basic")
            assert (await client.get("/dashboard", headers=basic("key"))).status == 200  # as a browser asks
            assert (await client.get("/dashboard", headers=basic("wrong"))).status == 401
            assert (await client.get("/dashboard", headers={"Authorization": "Basic !!"})).status == 401
            auth = {"Authorization": "Bearer key"}
            page = await (await client.get("/dashboard", headers=auth)).text()
            status = await (await client.get("/v1/status", headers=auth)).json()
            metrics = await (await client.get("/metrics", headers=auth)).text()
            return page, status, metrics

    page, status, metrics = asyncio.run(steps())
    assert "<script>" not in page and "&lt;script&gt;" in page and 'http-equiv="refresh"' in page
    assert status["queue"]["queued"] == 1 and 'scraper_jobs{state="queued"} 1' in metrics


def test_health_scores_what_is_really_there(site, auto_config, tmp_path):
    queue = SQLiteQueue(str(tmp_path / "q.db"))
    config = auto_config()
    bare = health(config, queue)
    assert len(bare) == 8 and verdict(bare) != "production-ready"

    site.add_raw("/", listing(*PRODUCTS), "text/html")
    crawl(config)
    queue.enqueue({})
    (tmp_path / "sites.json").write_text(json.dumps(
        {"127.0.0.1": {**REVIEWED, "tos_reviewed": date.today().isoformat()}}), encoding="utf-8")
    (tmp_path / "schedules.json").write_text(json.dumps(
        [{"name": "n", "cron": "0 2 * * *", "config": {}}]), encoding="utf-8")
    config.compliance.site_register = str(tmp_path / "sites.json")
    config.robots.user_agent = "AcmeBot/1.0 (+https://acme.test/bot)"
    config.rate_limit.requests_per_second = 1
    config.quality.schema, config.quality.dedupe, config.quality.provenance = PRODUCT_SCHEMA, "cross", True
    config.quality.min_items = 1
    config.alerts.enabled, config.alerts.webhook = True, "https://hooks.test/x"
    config.proxy.urls = ["http://p.test:1"]
    full = health(config, queue, str(tmp_path / "schedules.json"))
    assert {score.dimension: score.points for score in full} == dict.fromkeys((s.dimension for s in full), 2)
    assert verdict(full) == "production-ready"


def test_the_gate_finds_secrets_without_repeating_them(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    (tmp_path / "a.py").write_text(f'x = 1\nkey = "{key}"\npassword = "env:DB_PASSWORD_NAME"\n', encoding="utf-8")
    (tmp_path / "b.py").write_text('pass' + 'word = "correct-horse-battery"\n', encoding="utf-8")
    assert gate.find_secrets(tmp_path) == ["a.py:2", "b.py:1"]


def test_this_repository_passes_the_quick_gate():
    failed = [f"{check.name}: {check.detail}" for check in gate.gate(quick=True) if not check.passed]
    assert failed == []


# --- Search and recrawl -------------------------------------------------------------------------------------------------------


def test_pagerank_and_hits():
    graph = {"a": {"c"}, "b": {"c"}, "c": {"a"}, "d": set()}
    ranks = pagerank(graph)
    assert sum(ranks.values()) == pytest.approx(1) and max(ranks, key=ranks.get) == "c" and pagerank({}) == {}
    hubs, authorities = hits(graph)
    assert max(authorities, key=authorities.get) == "c" and hubs["a"] > hubs["d"]


def test_search_finds_words_and_near_words(tmp_path):
    store = SnapshotStore(str(tmp_path))

    def add(path, title, body, links=""):
        store.save(f"https://x.test/{path}", {"status_code": 200, "headers": {"content-type": "text/html"}, "content":
                   f"<html><head><title>{title}</title></head><body><p>{body}</p>{links}</body></html>"})

    add("kettles", "Copper kettles", "Boil water in a copper kettle.", '<a href="/teapots">t</a>')
    add("teapots", "Glass teapots", "Brew tea in a glass teapot.")
    add("about", "About", "We are a small shop.", '<a href="/teapots">t</a>')
    index = SearchIndex(str(tmp_path / "search.db"))
    assert index.build(store) == 3 and index.build(store) == 0
    assert index.search("copper")[0]["url"].endswith("/kettles")
    assert index.search("teapotz")[0]["url"].endswith("/teapots")  # no word matches; its letters do
    assert index.search('"; DROP TABLE pages --') is not None and index.search("!!!") == []
    assert index.top()[0]["url"].endswith("/teapots")
    add("kettles", "Enamel kettles", "Now in enamel.")
    assert index.build(store) == 1 and index.search("copper")[0]["title"] != "Copper kettles"


def test_recrawl_follows_how_often_a_page_changes():
    def interval(*visits):
        return recrawl_interval(SimpleNamespace(history=lambda _url: [
            {"fetched_at": at * 3600.0, "content_hash": digest} for at, digest in visits]), "u")

    assert interval((0, "a")) == UNKNOWN
    assert interval((0, "a"), (24, "b"), (48, "c")) == 12 * 3600
    assert interval((0, "a"), (1, "b"), (2, "c")) == SHORTEST
    assert interval((0, "a"), (24 * 60, "a")) == LONGEST


# --- The service: watch, interact, queued jobs ------------------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path):
    return ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)


def test_watch_reports_what_changed_since_last_time(site, service):
    def look(*products):
        site.add_raw("/", listing(*products), "text/html")

        async def once():
            try:
                return await service.watch(site.url + "/")
            finally:
                await service.close()
        return asyncio.run(once())

    first = look(*PRODUCTS)
    assert first["baseline"] and not first["changed_at_all"]
    assert not look(*PRODUCTS)["changed_at_all"]
    changed = look(("Kettle model A", "£9.00", "k1"), *PRODUCTS[2:], ("Kettle model Z", "£5.00", "kz"))
    assert changed["summary"] == {"new": 1, "changed": 1, "removed": 1, "unchanged": 3, "kept_from_unread_pages": 0}
    assert changed["changed"][0]["changes"]["price"] == {"before": "£1.00", "after": "£9.00"} and changed["text_changed"]


def test_interact_refuses_bad_steps_and_a_missing_browser(site, service):
    for steps, code in (([{"explode": 1}], "invalid_action"), ([], "browser_disabled")):
        with pytest.raises(ServiceError) as error:
            asyncio.run(service.interact(site.url + "/", steps))
        assert error.value.code == code


def test_crawls_are_queued_over_http_and_safety_settings_are_not_accepted(site, service, tmp_path):
    queue = SQLiteQueue(str(tmp_path / "q.db"))

    async def steps():
        async with TestClient(TestServer(create_app(service, queue=queue))) as client:
            for bad in ({"urls": ["ftp://x"]}, {"urls": [site.url], "login": ["a"]}, {"urls": ["--config=x"]}, []):
                assert (await client.post("/v1/jobs", json=bad)).status == 400, bad
            for path in ("/v1/jobs", "/v1/scrape"):  # what another website could send from a browser here
                response = await client.post(path, data='{"urls": ["x"]}', headers={"Content-Type": "text/plain"})
                assert response.status == 400 and "application/json" in (await response.json())["message"]
            response = await client.post("/v1/jobs", json={"urls": [site.url + "/"], "max_pages": 1, "mode": "static"})
            assert response.status == 202
            job = (await response.json())["id"]
            shown = await (await client.get(f"/v1/jobs/{job}")).json()
            assert shown["state"] == "queued" and (await client.get("/v1/jobs/nope")).status == 404
            assert (await client.get("/v1/jobs?state=odd")).status == 400
            assert len((await (await client.get("/v1/jobs")).json())["jobs"]) == 1
            assert (await client.post(f"/v1/jobs/{job}/cancel")).status == 200
            assert (await client.post(f"/v1/jobs/{job}/cancel")).status == 409

    asyncio.run(steps())
    assert queue.counts()["cancelled"] == 1 and not site.hits


def test_changes_are_pushed_to_subscribers(site, auto_config):
    received = []

    async def run():
        async def hook(request):
            received.append(await request.json())
            return web.Response()

        app = web.Application()
        app.router.add_post("/", hook)
        async with TestServer(app) as server:
            for products in (PRODUCTS, PRODUCTS[:4]):
                site.add_raw("/", listing(*products), "text/html")
                config = auto_config(track_changes=True)
                config.alerts.enabled, config.alerts.change_webhook = True, str(server.make_url("/"))
                await ProductionCrawler(config).crawl()

    asyncio.run(run())
    [sent] = received
    assert sent["text"] == "0 new, 0 changed, 1 removed" and sent["removed"][0]["title"] == "Kettle model E"


def test_the_crawler_module_exposes_no_way_to_disguise_itself():
    """The line this project holds: one honest identity, and a refusal is an answer."""
    source = Path(crawler_module.__file__).parents[1]
    text = "\n".join(path.read_text(encoding="utf-8") for folder in ("core", "fetcher", "jobqueue")
                     for path in (source / folder).glob("*.py")).lower()
    assert not any(word in text for word in ("navigator.webdriver", "playwright_stealth", "undetected", "2captcha", "curl_cffi"))
