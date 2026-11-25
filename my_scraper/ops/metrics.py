"""Counters, gauges and histograms in Prometheus text format. An OpenTelemetry collector reads it too."""
import threading
from typing import Dict, List, Tuple

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
Labels = Tuple[Tuple[str, str], ...]

HELP = {
    "scraper_pages_total": "Page outcomes, by status",
    "scraper_items_total": "Records extracted",
    "scraper_stage_seconds": "Time spent per pipeline stage",
    "scraper_proxy_requests_total": "Requests per proxy, by result",
    "scraper_proxy_up": "1 while a proxy is in use, 0 while it rests after failures",
    "scraper_circuits_open_total": "Sites left alone after too many failures",
    "scraper_quality_valid_ratio": "Share of records that passed validation in the last run",
    "scraper_quality_null_required_ratio": "Share of required values missing in the last run",
    "scraper_success_ratio": "Share of pages read successfully in the last run",
    "scraper_jobs": "Jobs in the queue, by state",
    "scraper_llm_calls_total": "Calls to the language model fallback, by result",
}


def _labels(labels: Dict[str, str]) -> Labels:
    return tuple(sorted((name, str(value)) for name, value in labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render(name: str, labels: Labels, value: float) -> str:
    inside = ",".join(f'{key}="{_escape(text)}"' for key, text in labels)
    return f"{name}{{{inside}}} {value:.12g}" if inside else f"{name} {value:.12g}"


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._values: Dict[str, Dict[Labels, float]] = {}
        self._types: Dict[str, str] = {}
        self._histograms: Dict[str, Dict[Labels, List[float]]] = {}  # bucket counts, then sum, then count

    def inc(self, name: str, amount: float = 1, **labels):
        with self._lock:
            self._types[name] = "counter"
            series = self._values.setdefault(name, {})
            series[_labels(labels)] = series.get(_labels(labels), 0) + amount

    def set(self, name: str, value: float, **labels):
        with self._lock:
            self._types[name] = "gauge"
            self._values.setdefault(name, {})[_labels(labels)] = value

    def observe(self, name: str, seconds: float, **labels):
        with self._lock:
            row = self._histograms.setdefault(name, {}).setdefault(_labels(labels), [0.0] * (len(BUCKETS) + 2))
            for i, limit in enumerate(BUCKETS):
                row[i] += seconds <= limit
            row[-2] += seconds
            row[-1] += 1

    def value(self, name: str, **labels) -> float:
        return self._values.get(name, {}).get(_labels(labels), 0)

    def render(self) -> str:
        lines = []
        with self._lock:
            for name, series in sorted(self._values.items()):
                lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} {self._types[name]}"]
                lines += [_render(name, labels, value) for labels, value in sorted(series.items())]
            for name, rows in sorted(self._histograms.items()):
                lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} histogram"]
                for labels, row in sorted(rows.items()):
                    for i, limit in enumerate(BUCKETS):
                        lines.append(_render(f"{name}_bucket", labels + (("le", f"{limit:g}"),), row[i]))
                    lines.append(_render(f"{name}_bucket", labels + (("le", "+Inf"),), row[-1]))
                    lines.append(_render(f"{name}_sum", labels, row[-2]))
                    lines.append(_render(f"{name}_count", labels, row[-1]))
        return "\n".join(lines) + "\n"


METRICS = Metrics()
