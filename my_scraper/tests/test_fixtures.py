"""Acceptance fixtures: saved pages and what must come out of them. A parser change that alters this needs a decision.

Versioned by folder: a new expectation gets a new folder, so older parsers can still be checked against theirs.
"""
import json
from pathlib import Path

import pytest
from parsel import Selector

from my_scraper.core.links import next_page_links
from my_scraper.core.page_checks import classify_page
from my_scraper.core.replay import replay
from my_scraper.parser.universal_parser import UniversalParser
from my_scraper.parser.version import parser_version
from my_scraper.storage.snapshots import SnapshotStore

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [(folder, name, entry) for folder in sorted(FIXTURES.iterdir())
         for name, entry in json.loads((folder / "expected.json").read_text(encoding="utf-8")).items()]


def fetched(folder, name):
    return {"url": "https://fixture.test/kettles", "final_url": "https://fixture.test/kettles", "status_code": 200,
            "content": (folder / name).read_text(encoding="utf-8"), "headers": {"content-type": "text/html"},
            "fetcher_type": "static"}


def without_text(items):
    """Main-text extraction is trafilatura's, and changes with its version."""
    return [{name: value for name, value in item.items() if name != "text"} for item in items]


@pytest.mark.parametrize("folder, name, entry", CASES, ids=[f"{folder.name}/{name}" for folder, name, _ in CASES])
def test_fixture_extracts_as_agreed(folder, name, entry):
    result = fetched(folder, name)
    assert classify_page(result) == entry["status"]
    assert next_page_links(Selector(text=result["content"], type="html")) == entry["next"]
    if entry["status"] is None:
        assert without_text(UniversalParser().parse_page(result).items) == without_text(entry["items"])


def test_a_missing_field_is_absent_and_shifts_nothing():
    items = UniversalParser().parse_page(fetched(FIXTURES / "v1", "missing_fields.html")).items
    assert [item.get("price") for item in items] == ["£45.00", None, "£28.00", "£12.75"]


def test_replaying_every_fixture_gives_the_same_records(tmp_path):
    store, parser = SnapshotStore(str(tmp_path)), UniversalParser()
    spec = {"selectors": {}, "item_selector": None, "include_markdown": False}
    for folder, name, _ in CASES:
        result = fetched(folder, name)
        snapshot = store.save(result["url"], result)
        store.save_extraction(snapshot, parser.parse_page(result).items, spec, parser_version())
    report, _ = replay(store, store.find())
    assert (report.replayed, report.same, report.errors) == (len(CASES), len(CASES), [])
