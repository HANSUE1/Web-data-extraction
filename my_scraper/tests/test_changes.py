"""Change tracking between runs: new, changed and removed items."""
import asyncio
import json
from pathlib import Path

from my_scraper.cli.main import main
from my_scraper.core.crawler import ProductionCrawler
from my_scraper.storage.changes import compare, describe, snapshot_path, track


def product_list(*products):
    cards = "".join(
        f'<div class="card"><h3><a href="/p/{slug}">{name}</a></h3><span class="price">{price}</span></div>'
        for slug, name, price in products
    )
    return f"<html><body><main>{cards}</main></body></html>"


def crawl(config):
    crawler = ProductionCrawler(config)
    asyncio.run(crawler.crawl())
    return crawler


def tracked_config(make_config, **overrides):
    config = make_config(item_selector=".card", track_changes=True, max_depth=0, **overrides)
    config.selectors = {"title": "h3 a::text", "price": ".price::text", "link": "h3 a::attr(href)"}
    return config


def test_first_run_saves_a_baseline_and_writes_no_report(site, make_config):
    site.add_raw("/", product_list(("a", "Alpha", "$10")), "text/html")

    crawler = crawl(tracked_config(make_config))

    assert crawler.changes.baseline
    assert crawler.changes.report_path is None
    assert Path(crawler.changes.snapshot_path).exists()
    assert not list(Path("output").glob("changes_*.json"))


def test_second_run_reports_new_changed_and_removed_items(site, make_config):
    site.add_raw("/", product_list(("a", "Alpha", "$10"), ("b", "Beta", "$20"), ("c", "Gamma", "$30")), "text/html")
    crawl(tracked_config(make_config))

    site.add_raw("/", product_list(("a", "Alpha", "$8"), ("c", "Gamma", "$30"), ("d", "Delta", "$40")), "text/html")
    report = crawl(tracked_config(make_config)).changes

    assert [item["title"] for item in report.new] == ["Delta"]
    assert [item["title"] for item in report.removed] == ["Beta"]
    assert [(entry["item"]["title"], entry["changes"]) for entry in report.changed] == [
        ("Alpha", {"price": {"before": "$10", "after": "$8"}})
    ]
    assert report.unchanged == 1

    saved = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert saved["summary"]["new"] == 1 and saved["summary"]["removed"] == 1
    assert saved["since"] == report.since


def test_items_on_pages_not_read_again_are_kept_not_removed(site, make_config):
    site.add_raw("/", product_list(("a", "Alpha", "$10")) + '<a href="/more">more</a>', "text/html")
    site.add_raw("/more", product_list(("z", "Zeta", "$99")), "text/html")
    crawl(_with_depth(make_config, 1))

    site.pages["/more"] = (500, "down", "text/html")
    report = crawl(_with_depth(make_config, 1)).changes

    assert report.removed == []
    assert report.kept == 1
    snapshot = json.loads(Path(report.snapshot_path).read_text(encoding="utf-8"))
    assert sorted(item["title"] for item in snapshot["items"]) == ["Alpha", "Zeta"]

    # When the page comes back without Zeta, then it really is gone.
    site.add_raw("/more", product_list(("y", "Eta", "$5")), "text/html")
    report = crawl(_with_depth(make_config, 1)).changes
    assert [item["title"] for item in report.removed] == ["Zeta"]
    assert [item["title"] for item in report.new] == ["Eta"]


def _with_depth(make_config, depth):
    config = tracked_config(make_config)
    config.max_depth = depth
    config.retry.max_retries = 0
    return config


def test_different_crawls_keep_separate_snapshots():
    one = snapshot_path(["https://a.example/"], {"title": "h1"}, None)
    other = snapshot_path(["https://a.example/"], {"title": "h2"}, None)
    assert one != other
    assert one == snapshot_path(["https://a.example/"], {"title": "h1"}, None)
    assert one.name.startswith("a.example_")


def test_table_rows_without_titles_are_matched_by_their_first_column():
    page = "https://data.example/"
    before = [{"url": page, "country": "India", "population": 1}, {"url": page, "country": "Chad", "population": 2}]
    after = [{"url": page, "country": "Chad", "population": 3}, {"url": page, "country": "India", "population": 1}]

    report = compare(before, after, {page})

    assert report.new == [] and report.removed == []
    assert report.changed[0]["changes"] == {"population": {"before": 2, "after": 3}}


def test_repeated_identical_rows_are_counted_not_collapsed():
    page = "https://data.example/"
    row = {"url": page, "title": "Same"}
    report = compare([row], [row, row], {page})
    assert len(report.new) == 1 and report.unchanged == 1


def test_which_method_found_an_item_is_not_a_change():
    page = "https://x.example/"
    report = compare([{"url": page, "title": "T", "source": "list"}],
                     [{"url": page, "title": "T", "source": "json-ld"}], {page})
    assert report.changed == [] and report.unchanged == 1


def test_a_corrupt_snapshot_is_treated_as_a_first_run(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text("{not json", encoding="utf-8")
    report = track([{"url": "https://x.example/", "title": "T"}], path, str(tmp_path))
    assert report.baseline
    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["title"] == "T"


def test_summary_lines_name_the_changes(tmp_path):
    page = "https://x.example/"
    path = tmp_path / "snap.json"
    track([{"url": page, "title": "Kettle", "price": "£20"}], path, str(tmp_path))
    report = track([{"url": page, "title": "Kettle", "price": "£15"}, {"url": page, "title": "Toaster"}],
                   path, str(tmp_path))

    lines = describe(report)

    assert "1 new, 1 changed, 0 removed" in lines[0]
    assert any("Kettle" in line and "£20 -> £15" in line for line in lines)
    assert any(line.strip() == "+ Toaster" for line in lines)


def test_cli_track_prints_the_change_summary(site, capsys):
    site.add_raw("/", product_list(("a", "Alpha", "$10")), "text/html")
    argv = ["--urls", site.url + "/", "--no-browser", "--max-depth", "0", "--quiet", "--track",
            "--rate-limit", "1000", "--item", ".card", "--selector", "title=h3 a::text",
            "--selector", "price=.price::text"]
    main(argv)
    assert "first run, baseline saved" in capsys.readouterr().out

    site.add_raw("/", product_list(("a", "Alpha", "$12")), "text/html")
    main(argv)
    out = capsys.readouterr().out
    assert "0 new, 1 changed, 0 removed" in out
    assert "$10 -> $12" in out
