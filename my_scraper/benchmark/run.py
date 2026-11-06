"""Measure how the scraper does on real websites of very different kinds.

Runs the real command line tool once per site, one page each, obeying robots.txt:

    python my_scraper/benchmark/run.py --out results.json
    python my_scraper/benchmark/run.py --only javascript,file
    python my_scraper/benchmark/run.py --out new.json --compare old.json

A count proves nothing: a page of menu links has plenty of items. So a site is only
"ok" when the content passes the checks in sites.json: how long titles should be,
which fields must be present, and text known to be on the page. "partial" means
something came back but it failed those checks. "refused" means the site or its
robots.txt said no, which the scraper reports and respects.
"""
import argparse
import asyncio
import collections
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "my_scraper" / "cli" / "main.py"
SITES = Path(__file__).with_name("sites.json")

PARALLEL = 6
SITE_TIMEOUT = 150  # seconds

REFUSED_STATUSES = {"robots_disallowed", "robots_unreachable", "blocked_by_site", "login_required", "site_abandoned",
                    "http_error", "off_site_redirect", "consent_wall"}
META_FIELDS = {"url", "source", "source_url", "type"}


def read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def junk_ratio(items: list) -> float:
    """Share of items with replacement characters, which means text decoded wrongly."""
    if not items:
        return 0.0
    garbled = sum(1 for item in items if "\ufffd" in json.dumps(item, ensure_ascii=False))
    return garbled / len(items)


def visual_length(text: str) -> int:
    """Length that treats CJK characters as wide, since they carry about two letters' worth."""
    return sum(2 if ord(character) >= 0x2E80 else 1 for character in text)


def problems_with(site: dict, items: list) -> list:
    """Everything wrong with the items, judged against what this site should give."""
    problems = []
    expect = site["expect"]
    fields = [key for item in items for key in item if key not in META_FIELDS]
    text = json.dumps(items, ensure_ascii=False)

    if junk_ratio(items) >= 0.1:
        problems.append("garbled text")
    if site.get("contains") and site["contains"] not in text:
        problems.append(f"missing {site['contains']!r}")

    if expect == "document":
        if len(items) > 3:
            problems.append(f"expected one document, got {len(items)} items")
        if max((len(str(item.get("text", ""))) for item in items), default=0) < 300:
            problems.append("no real text")
        return problems

    if len(items) < site["min_items"]:
        problems.append(f"only {len(items)} of {site['min_items']} items")

    if expect == "table":
        if len(fields) < 2 * len(items):
            problems.append("rows have too few columns")
        return problems
    if expect == "any":
        return problems

    titles = [str(item.get("title") or "") for item in items]
    lengths = sorted(visual_length(title) for title in titles)
    median = lengths[len(lengths) // 2] if lengths else 0
    if median < site.get("min_title", 0):
        problems.append(f"titles too short (median {median})")
    if titles and len(set(titles)) < 0.7 * len(titles):
        problems.append("titles repeat")
    if titles and sum(1 for title in titles if title and not any(c.isalpha() for c in title)) > 0.2 * len(titles):
        problems.append("titles are numbers")
    for field in site.get("need", []):
        if sum(1 for item in items if item.get(field)) < 0.6 * len(items):
            problems.append(f"few items have {field}")
    return problems


def judge(site: dict, items: list, statuses: dict) -> tuple:
    if not items:
        return ("refused" if set(statuses) & REFUSED_STATUSES else "failed"), []
    problems = problems_with(site, items)
    return ("partial" if problems else "ok"), problems


async def run_site(site: dict, limit: asyncio.Semaphore) -> dict:
    async with limit:
        workdir = Path(tempfile.mkdtemp(prefix="bench_"))
        started = time.time()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(CLI), "--urls", site["url"], "--max-pages", "1", "--max-depth", "0",
            "--no-pagination", "--export", "jsonl",
            cwd=workdir, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), SITE_TIMEOUT)
            timed_out = False
        except asyncio.TimeoutError:
            process.kill()
            output, _ = await process.communicate()
            timed_out = True

        exports = list((workdir / "output").glob("*.jsonl")) if (workdir / "output").exists() else []
        items = read_jsonl(exports[0]) if exports else []

        statuses: dict = collections.Counter()
        errors = []
        audit = workdir / "logs" / "audit.log"
        if audit.exists():
            for event in read_jsonl(audit):
                statuses[event.get("status")] += 1
                if event.get("error"):
                    errors.append(event["error"][:160])

        fetchers = set()
        log_file = workdir / "logs" / "scraper.log"
        if log_file.exists():
            for entry in read_jsonl(log_file):
                if entry.get("message") == "http_request":
                    fetchers.add(entry.get("fetcher_type"))

        verdict, problems = judge(site, items, dict(statuses))
        result = {
            **site,
            "verdict": "timeout" if timed_out and not items else verdict,
            "problems": problems,
            "items": len(items),
            "sources": dict(collections.Counter(item.get("source") for item in items)),
            "statuses": dict(statuses),
            "fetchers": sorted(filter(None, fetchers)),
            "seconds": round(time.time() - started, 1),
            "garbled": round(junk_ratio(items), 2),
            "errors": errors[:2],
            "samples": [str(item.get("title") or next(iter(item.values()), ""))[:70] for item in items[:3]],
            "exit_tail": "" if process.returncode == 0 else output.decode("utf-8", "replace")[-300:],
        }
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"{result['verdict']:8} {site['name']:22} items={result['items']:<4} "
              f"{result['seconds']:>5}s {'/'.join(result['fetchers'])}", flush=True)
        return result


async def run_all(sites: list) -> list:
    limit = asyncio.Semaphore(PARALLEL)
    return await asyncio.gather(*(run_site(site, limit) for site in sites))


def summarize(results: list):
    verdicts = collections.Counter(result["verdict"] for result in results)
    usable = [result for result in results if result["verdict"] != "refused"]
    ok = verdicts["ok"]
    print(f"\n{len(results)} sites: " + ", ".join(f"{name} {count}" for name, count in verdicts.most_common()))
    if usable:
        print(f"Worked on {ok} of the {len(usable)} sites that allow access ({100 * ok // len(usable)}%)")

    groups = collections.defaultdict(collections.Counter)
    for result in results:
        groups[result["group"]][result["verdict"]] += 1
    for group, counts in sorted(groups.items()):
        print(f"  {group:12} " + " ".join(f"{name}={count}" for name, count in counts.most_common()))

    print("\nNot ok:")
    for result in results:
        if result["verdict"] != "ok":
            detail = result["problems"] or result["errors"][:1] or result["samples"][:1]
            print(f"  {result['verdict']:8} {result['name']:22} items={result['items']} "
                  f"{result['statuses']} {detail}"[:170])


def compare(results: list, previous_file: str):
    previous = {result["name"]: result for result in json.loads(Path(previous_file).read_text(encoding="utf-8"))}
    print("\nChanged since last run:")
    for result in results:
        before = previous.get(result["name"])
        if before and (before["verdict"], before["items"]) != (result["verdict"], result["items"]):
            print(f"  {result['name']:22} {before['verdict']}/{before['items']} -> {result['verdict']}/{result['items']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="Save the full results as JSON")
    parser.add_argument("--only", help="Comma-separated site names or groups")
    parser.add_argument("--sites", default=str(SITES), help="Site list. sites_holdout.json holds sites nothing was tuned on")
    parser.add_argument("--compare", metavar="FILE", help="Show what changed since an earlier --out file")
    args = parser.parse_args()

    sites = json.loads(Path(args.sites).read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        sites = [site for site in sites if site["name"] in wanted or site["group"] in wanted]

    results = asyncio.run(run_all(sites))
    summarize(results)
    if args.compare:
        compare(results, args.compare)
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
