"""Parse stored snapshots again and compare with what was extracted at the time. No request is made.

Three uses: test a parser change against everything ever crawled, recover pages that failed to
parse, and go back to an older parser, which runs from git beside the current one.
"""
import base64
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from my_scraper.parser.universal_parser import UniversalParser
from my_scraper.parser.version import parser_version
from my_scraper.storage.changes import compare, page_of
from my_scraper.storage.snapshots import SnapshotStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = 10
REV_TIMEOUT = 600

# Runs inside the older checkout, so it uses that version's parser and nothing of this one.
OLD_PARSER = """
import base64, json, sys
sys.stdin.reconfigure(encoding="utf-8"); sys.stdout.reconfigure(encoding="utf-8")
from my_scraper.parser.universal_parser import UniversalParser
parser = UniversalParser()
for line in sys.stdin:
    job = json.loads(line)
    result, spec = job["result"], job["spec"]
    if "body" in result:
        result["body"] = base64.b64decode(result["body"])
    try:
        page = parser.parse_page(result, spec["selectors"], spec["item_selector"], spec["include_markdown"])
        answer = {"items": page.items}
    except Exception as e:
        answer = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(answer, default=str), flush=True)
"""

NO_SPEC = {"selectors": {}, "item_selector": None, "include_markdown": False}


class ReplayError(Exception):
    """The replay could not be run."""


@dataclass
class ReplayReport:
    parser: str
    replayed: int = 0
    same: int = 0
    changed: List[Dict[str, Any]] = field(default_factory=list)
    recovered: int = 0          # pages with no extraction on record, as after a parse failure
    skipped: int = 0            # records from the language model, which is not run again
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"parser": self.parser, "replayed": self.replayed, "same": self.same, "changed": len(self.changed),
                "recovered": self.recovered, "skipped": self.skipped, "errors": self.errors[:EXAMPLES],
                "examples": self.changed[:EXAMPLES]}


def _parse_now(jobs: Iterable[Tuple[Dict, Dict]]) -> Iterator[Dict[str, Any]]:
    parser = UniversalParser()
    for result, spec in jobs:
        try:
            page = parser.parse_page(result, spec["selectors"], spec["item_selector"], spec["include_markdown"])
            yield {"items": page.items}
        except Exception as e:  # the point of a replay is to find these
            yield {"error": f"{type(e).__name__}: {e}"}


def _parse_at(revision: str, jobs: Iterable[Tuple[Dict, Dict]]) -> Iterator[Dict[str, Any]]:
    """Parse with the code of a git revision, checked out into a temporary folder."""
    archive = subprocess.run(["git", "archive", "--format=tar", revision, "my_scraper"], cwd=PROJECT_ROOT,
                             capture_output=True, check=False)
    if archive.returncode:
        raise ReplayError(f"git cannot read revision {revision!r}: {archive.stderr.decode(errors='replace').strip()}")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(folder, filter="data")
        lines = []
        for result, spec in jobs:
            if "body" in result:
                result = {**result, "body": base64.b64encode(result["body"]).decode("ascii")}
            lines.append(json.dumps({"result": result, "spec": spec}))
        run = subprocess.run([sys.executable, "-c", OLD_PARSER], cwd=folder, input="\n".join(lines), text=True,
                             encoding="utf-8", capture_output=True, timeout=REV_TIMEOUT, check=False)
        if run.returncode:
            raise ReplayError(f"the parser of {revision} failed: {run.stderr.strip().splitlines()[-1:]}")
        for line in run.stdout.splitlines():
            yield json.loads(line)


def _plain(items: Any) -> Any:
    return json.loads(json.dumps(items, ensure_ascii=False, sort_keys=True, default=str))


def replay(store: SnapshotStore, snapshots: List[Dict[str, Any]], revision: Optional[str] = None,
           save: bool = False) -> Tuple[ReplayReport, List[Dict[str, Any]]]:
    """Parse snapshots again. Returns the report and every record, for exporting."""
    report = ReplayReport(parser=revision or parser_version())
    chosen, before = [], []
    for snapshot in snapshots:
        if (snapshot["status"] or 0) >= 400:
            continue
        extraction = store.extraction(snapshot["id"])
        spec = extraction["spec"] if extraction else NO_SPEC
        if "llm" in spec:
            report.skipped += 1
            continue
        chosen.append((snapshot, spec))
        before.append(extraction)

    jobs = ((store.fetch_result(snapshot), spec) for snapshot, spec in chosen)
    answers = _parse_at(revision, jobs) if revision else _parse_now(jobs)
    records: List[Dict[str, Any]] = []
    for (snapshot, spec), extraction, answer in zip(chosen, before, answers, strict=True):
        report.replayed += 1
        if "error" in answer:
            report.errors.append({"snapshot_id": snapshot["id"], "url": snapshot["url"], "error": answer["error"]})
            continue
        items = _plain(answer["items"])
        records += [{"url": snapshot["url"], **item, "_meta": {
            "snapshot_id": snapshot["id"], "parser_version": report.parser}} for item in items]
        if extraction is None:
            report.recovered += 1
        elif items == extraction["items"]:
            report.same += 1
        else:
            pages = {page for page in map(page_of, extraction["items"])}
            diff = compare(extraction["items"], items, pages)
            report.changed.append({"snapshot_id": snapshot["id"], "url": snapshot["url"],
                                   "was_parsed_by": extraction["parser_version"], **diff.to_dict()["summary"],
                                   "changes": diff.changed[:3]})
        if save and not revision:
            store.save_extraction(snapshot["id"], items, spec, report.parser)
    return report, records
