"""Run the real entry points as subprocesses, the way a user would."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_python(*args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


def cli_as_module(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-m", "my_scraper.cli.main", *map(str, args)], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def cli_as_script(*args):
    return run_python(REPO_ROOT / "my_scraper" / "cli" / "main.py", *args)


@pytest.mark.parametrize("cli", [cli_as_module, cli_as_script])
def test_cli_crawls_and_exports(site, cli):
    site.add("/", "home", links=["/a"])
    site.add("/a", "page a")

    result = cli("--urls", site.url + "/", "--rate-limit", "100", "--export", "jsonl", "csv",
                 "--output-dir", "results", "--selector", "heading=h1::text")

    assert result.returncode == 0, result.stderr
    assert "Extracted 2 items from 2 pages" in result.stdout

    exported = list(Path("results").glob("*.jsonl"))
    assert len(exported) == 1
    items = [json.loads(line) for line in exported[0].read_text(encoding="utf-8").splitlines()]
    assert sorted(item["heading"] for item in items) == ["home", "page a"]
    assert len(list(Path("results").glob("*.csv"))) == 1


def test_cli_loads_a_nested_config_file(site):
    site.add("/", "home")
    Path("config.json").write_text(json.dumps({
        "start_urls": [site.url + "/"],
        "fetcher": "static",
        "selectors": {"heading": "h1::text"},
        "export_formats": ["json"],
        "rate_limit": {"requests_per_second": 100},
    }), encoding="utf-8")

    result = cli_as_module("--config", "config.json")

    assert result.returncode == 0, result.stderr
    assert len(list(Path("output").glob("*.json"))) == 1


def test_cli_rate_limit_below_one(site):
    site.add("/", "home")

    result = cli_as_module("--urls", site.url + "/", "--rate-limit", "0.5")

    assert result.returncode == 0, result.stderr
    assert "Extracted 1 items" in result.stdout


@pytest.mark.parametrize("args, message", [
    ([], "no start URLs"),
    (["--resume"], "no start URLs"),
    (["--urls", "example.com"], "must begin with http"),
    (["--urls", "http://x.test/", "--selector", "broken"], "NAME=SELECTOR"),
    (["--config", "missing.json"], "could not load config"),
])
def test_cli_explains_bad_input(args, message):
    result = cli_as_module(*args)

    assert result.returncode == 2
    assert message in result.stderr


def test_example_script_imports_from_any_folder():
    code = ("import runpy, sys; sys.argv=['x']; "
            f"m = runpy.run_path(r'{REPO_ROOT / 'my_scraper' / 'example_usage.py'}', run_name='check'); "
            "print(m['QuickBooksScraper'].__name__)")
    result = run_python("-c", code)

    assert result.returncode == 0, result.stderr
    assert "QuickBooksScraper" in result.stdout


def test_parse_product_extracts_a_book():
    sys.path.insert(0, str(REPO_ROOT))
    from my_scraper.example_usage import parse_product

    html = """
    <div class="product_main"><h1> A Book </h1><p class="price_color">£51.77</p>
      <p class="star-rating Three"></p></div>
    <div id="product_gallery"><img src="../media/cover.jpg"></div>
    <div id="product_description"></div><p>A description</p>
    <table class="table">
      <tr><th>UPC</th><td>abc</td></tr><tr><th>Type</th><td>Books</td></tr>
      <tr><th>Excl</th><td>£1.00</td></tr><tr><th>Incl</th><td>£2.00</td></tr>
      <tr><th>Tax</th><td>£0.00</td></tr><tr><th>Avail</th><td>In stock (22 available)</td></tr>
      <tr><th>Reviews</th><td>7</td></tr>
    </table>"""

    book = parse_product(html, "https://books.toscrape.com/catalogue/a/index.html")

    assert book["title"] == "A Book"
    assert book["price"] == "51.77"
    assert book["rating_num"] == 3
    assert book["availability_qty"] == 22
    assert book["num_reviews"] == 7
    assert book["image"] == "https://books.toscrape.com/catalogue/media/cover.jpg"


def simple_scraper(*args):
    return run_python(REPO_ROOT / "scrape_simple.py", *args)


def test_scrape_simple_extracts_items(site):
    site.add_raw("/list/", '<div class="r"><a href="/1"> One </a></div><div class="r"><a href="/2">Two</a></div>',
                 "text/html")

    result = simple_scraper("--url", site.url + "/list/", "--item", ".r",
                            "--field", "name:a::text", "--field", "link:a::attr(href)")

    assert result.returncode == 0, result.stderr
    saved = next(Path("outputs").glob("*/list.json"))  # /list/ names the file, not 'index'
    assert json.loads(saved.read_text(encoding="utf-8")) == [
        {"name": "One", "link": "/1"}, {"name": "Two", "link": "/2"},
    ]


def test_scrape_simple_rejects_malformed_field(site):
    result = simple_scraper("--url", site.url + "/", "--item", ".r", "--field", "noselector")

    assert result.returncode == 2
    assert "name:selector" in result.stderr


@pytest.mark.parametrize("extra_args, expected", [
    ([], "api"),
    (["--item", ".quote", "--field", "text:.text::text"], "selectors"),
])
def test_scrape_simple_on_quotes_site_honours_selectors(monkeypatch, extra_args, expected):
    sys.path.insert(0, str(REPO_ROOT))
    import scrape_simple

    used = []
    monkeypatch.setattr(scrape_simple, "scrape_quotes_api", lambda: used.append("api") or [])
    monkeypatch.setattr(scrape_simple, "scrape_generic", lambda *a: used.append("selectors") or [])
    monkeypatch.setattr(sys, "argv", ["x", "--url", "https://quotes.toscrape.com/", *extra_args])

    scrape_simple.main()

    assert used == [expected]


def test_check_output_summarises_newest_file():
    Path("output").mkdir()
    Path("output/run.jsonl").write_text('{"title": null, "price": "£1"}\n', encoding="utf-8")

    result = run_python(REPO_ROOT / "check_output.py")

    assert result.returncode == 0, result.stderr
    assert "1 lines" in result.stdout
