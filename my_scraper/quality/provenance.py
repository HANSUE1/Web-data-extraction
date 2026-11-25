"""Where every value of a record came from: its snapshot, parser, selector and place in the page."""
from typing import Any, Dict, Optional

META = "_meta"
MIN_LENGTH = 4  # a shorter value stands in many places, and the first is hardly ever the right one


class PageProvenance:
    """Provenance for the records of one page."""

    def __init__(self, fetch_result: Dict[str, Any], selectors: Dict[str, str], item_selector: Optional[str],
                 parser_version: str, schema_version: str, run_id: str):
        self.url = fetch_result.get("final_url") or fetch_result["url"]
        self.snapshot_id = fetch_result.get("snapshot_id")
        self.selectors = {name.removesuffix("[]"): selector for name, selector in selectors.items()}
        self.item_selector = item_selector
        self.common = {"snapshot_id": self.snapshot_id, "parser_version": parser_version,
                       "schema_version": schema_version, "run_id": run_id}
        # Offsets are into the UTF-8 text of the snapshot, as replay reads it.
        self._body = fetch_result["content"].encode("utf-8", "replace")

    def _place(self, value: Any) -> Dict[str, Any]:
        """Where the value first stands word for word, and how often it does. None when it was put together or decoded."""
        needle = str(value).encode("utf-8", "replace") if isinstance(value, (str, int, float)) else b""
        count = self._body.count(needle) if needle and not isinstance(value, bool) else 0
        if not count or (count > 1 and len(needle) < MIN_LENGTH):
            return {"bytes": None}
        start = self._body.find(needle)
        return {"bytes": [start, start + len(needle)], "occurrences": count}

    def of(self, record: Dict[str, Any]) -> Dict[str, Any]:
        fields = {}
        for name, value in record.items():
            if name in ("url", "source", "source_url", META) or value in (None, "", []):
                continue
            path = self.selectors.get(name)
            if path and self.item_selector:
                path = f"{self.item_selector} >> {path}"
            fields[name] = {"url": self.url, "snapshot_id": self.snapshot_id,
                            "selector": path or f"auto:{record.get('source', 'unknown')}", **self._place(value)}
        return {**self.common, "fields": fields}


def merge(into: Dict[str, Any], new: Dict[str, Any]):
    """Provenance of a record put together from two pages: each field keeps its own origin."""
    into["fields"] = {**into["fields"], **new["fields"]}
