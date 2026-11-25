"""Schema validation, a quality score per run, and drift against earlier runs."""
from statistics import median
from typing import Any, Dict, Iterable, List, Tuple

from jsonschema import Draft202012Validator, FormatChecker

Item = Dict[str, Any]

EMPTY = (None, "", [], {})
META = "_meta"
MAX_FIELDS = 60
MIN_HISTORY = 3          # earlier runs needed before "usual" means anything
FILL_DROP = 0.3          # a field found in this much smaller a share of records has drifted
COUNT_DROP = 0.5


class SchemaValidator:
    """Checks records against a JSON Schema."""

    def __init__(self, schema: Dict[str, Any]):
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def errors(self, item: Item) -> List[str]:
        record = {name: value for name, value in item.items() if name != META}
        return [f"{'.'.join(map(str, error.absolute_path)) or 'record'}: {error.message}"
                for error in self._validator.iter_errors(record)]

    def split(self, items: Iterable[Item]) -> Tuple[List[Item], List[Dict[str, Any]]]:
        """(valid records, rejected ones with the reasons)."""
        valid, rejected = [], []
        for item in items:
            errors = self.errors(item)
            if errors:
                rejected.append({"record": item, "errors": errors})
            else:
                valid.append(item)
        return valid, rejected


def quality_of(items: List[Item], rejected: int, pages: int, required: List[str]) -> Dict[str, Any]:
    total = len(items) + rejected
    names = sorted({name for item in items for name in item if name != META})[:MAX_FIELDS]
    cells = len(items) * len(required)
    nulls = sum(item.get(name) in EMPTY for item in items for name in required)
    return {
        "items": len(items),
        "rejected": rejected,
        "valid_ratio": round(len(items) / total, 4) if total else None,
        "null_required_ratio": round(nulls / cells, 4) if cells else 0.0,
        "items_per_page": round(total / pages, 4) if pages else 0.0,
        "fill": {name: round(sum(item.get(name) not in EMPTY for item in items) / len(items), 4) for name in names},
    }


def threshold_failures(quality: Dict[str, Any], min_items: int, min_valid_ratio: float,
                       max_null_ratio: float) -> List[str]:
    """Why this dataset should not be published."""
    failures = []
    if quality["items"] < min_items:
        failures.append(f"{quality['items']} items, at least {min_items} expected")
    if quality["valid_ratio"] is not None and quality["valid_ratio"] < min_valid_ratio:
        failures.append(f"{quality['valid_ratio']:.0%} of records are valid, at least {min_valid_ratio:.0%} expected")
    if quality["null_required_ratio"] > max_null_ratio:
        failures.append(f"{quality['null_required_ratio']:.0%} of required values are missing, "
                        f"at most {max_null_ratio:.0%} allowed")
    return failures


def drift(quality: Dict[str, Any], earlier: List[Dict[str, Any]]) -> List[str]:
    """What this run found much less of than earlier runs of the same crawl usually do."""
    if len(earlier) < MIN_HISTORY:
        return []
    found = []
    usual = median(run["items_per_page"] for run in earlier)
    if usual and quality["items_per_page"] < usual * (1 - COUNT_DROP):
        found.append(f"items per page fell from {usual:g} to {quality['items_per_page']:g}")
    for name in sorted({name for run in earlier for name in run["fill"]}):
        before = median(run["fill"].get(name, 0.0) for run in earlier)
        now = quality["fill"].get(name, 0.0)
        if before - now > FILL_DROP:
            found.append(f"field {name!r} was filled in {before:.0%} of records, now {now:.0%}")
    return found
