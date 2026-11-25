# Production coverage

Each requirement of a production-grade scraper, where it is met, and where it deliberately is not.

## 1. Legal and ethical foundation

| Requirement | Where |
| --- | --- |
| robots.txt, Crawl-delay, fail safe | `utils/politeness.py`. Unreachable means not fetched |
| Robots meta tags, X-Robots-Tag | `core/links.py` `robots_directives`. `noindex` pages are not stored, `nofollow` links not followed. `noarchive` is about showing cached copies to others, so it does not stop private snapshots |
| Terms of service review, API first | `compliance/risk.py`, `config/sites.example.json`, `ops assess` |
| Personal data: detect, flag, redact | `compliance/manager.py`, `compliance.redact_pii` |
| Geographic rules | `compliance.allowed_countries`, `denied_countries`. By country ending of the domain, so `.com` sites match no country |
| Append-only audit log | `outputs/logs/audit.log` |
| Go or no-go rules | `compliance/risk.py` `decide`, enforced by `compliance.require_go` |

## 2. Architecture

| Requirement | Where |
| --- | --- |
| Scheduler, durable queue, workers | `jobqueue/`: SQLite or Redis, leases, priorities, cron and off-peak windows |
| Not triggered by web requests | `POST /v1/jobs` queues a crawl. `/v1/scrape` and the like answer one interactive read |
| Tiered fetching | `fetcher/smart_fetcher.py`: HTTP, then browser, then observed steps (`fetcher/actions.py`) |
| Immutable snapshots, replay | `storage/snapshots.py`, `core/replay.py` |
| Acquisition apart from extraction | A page that fails to parse is replayed from its snapshot, never fetched again |

## 3. Access

| Requirement | Where |
| --- | --- |
| Proxies, health checks, usage | `fetcher/proxies.py`. A site keeps its proxy, and moves only when that proxy cannot be reached |
| Consistent browser profile | `browser.locale`, `timezone`, viewport, one user agent |
| Per-origin limits, circuit breakers | `concurrency.*`, `rate_limit.*`, `ProductionCrawler._note_outcome` |
| Blocks are stop signals | `core/page_checks.py`, `jobqueue/worker.py` |
| CAPTCHA solving, stealth | **Excluded on purpose.** The legal risk was weighed and declined. A test keeps it that way |

## 4. Extraction and quality

| Requirement | Where |
| --- | --- |
| Selector hierarchy | `parser/selectors.py`: `role=`, `label=`, CSS, XPath, `text=/…/`, with `||` alternatives |
| Selector cache and drift | `outputs/state/selectors.json`, per site. `selector_drift` in the log |
| Schema-bound language model fallback | `parser/llm.py`: off by default, model pinned, answers checked against the page, every call audited |
| Validation, normalization | `quality/report.py` (JSON Schema), `quality/normalize.py`. Exchange rates are supplied, never guessed |
| Deduplication | `quality/dedup.py`: exact and SimHash, within and across runs |
| Provenance and versions | `quality/provenance.py`: snapshot, parser, schema, selector and byte range per field |

## 5. Storage

| Requirement | Where |
| --- | --- |
| Pluggable backends | Files, SQLite, PostgreSQL (`storage/database.py`), S3 snapshots (`storage/blobs.py`) |
| MongoDB, Elasticsearch | Through `storage.sinks`, your own `module:function`. None is bundled |
| Dataset contract | Manifest per run, `snapshot` / `append` / `upsert`, quality thresholds before publication |

## 6. Resilience

| Requirement | Where |
| --- | --- |
| Retries, Retry-After, permanent errors | `utils/retry.py` |
| Circuit breaker per origin | By refusals in a row and by error rate |
| Dead letters | URLs: `ops dlq`. Jobs: `ops jobs --state dead` |
| Checkpoints, resuming | `CrawlState`, and leases in `jobqueue/` |

## 7. Observability

| Requirement | Where |
| --- | --- |
| Metrics | `/metrics` in Prometheus format, which OpenTelemetry collectors read too. `outputs/logs/metrics.prom` for single runs |
| Structured logs | JSON, with `run_id`, `job_id`, `snapshot_id` and `stage` |
| Alerts | `ops/alerts.py`: success rate over a window, quality, drift, dead letters, queue depth |
| Health score | `ops health` |
| Live dashboard | `/dashboard`, and the studio for a single crawl |

## 8. Scaling

| Requirement | Where |
| --- | --- |
| Distributed workers | `SCRAPER_QUEUE_URL=redis://…`, `--fan-out`, priorities, pacing agreed between workers |
| HTTP first, conditional requests | `fetcher/smart_fetcher.py`, `storage/etag_cache.py` |
| Off-peak | `window` and `cron` in the schedules |

Run history, cross-run deduplication and the HTTP cache are per state folder. Workers on one machine share
one, workers on several machines each keep their own. Delivery by key (`upsert`) is shared either way.

## 9. Testing

| Requirement | Where |
| --- | --- |
| Acceptance fixtures | `my_scraper/tests/fixtures/`, versioned by folder |
| Replay tests | `test_fixtures.py`, `ops replay --strict` |
| Production gate | `ops gate` |

## 10. Deployment

| Requirement | Where |
| --- | --- |
| Containers, health checks, limits | `Dockerfile`, `docker-compose.yml`, `deploy/kubernetes.yaml` |
| CI/CD | `.github/workflows/ci.yml`: lint, tests on real PostgreSQL and Redis, gate, image build and health check, publish on a `v*` tag |
| Secrets | `config/secret_refs.py` (`env:`, `file:`), scanned for by the gate |

## 11. Maintenance

| Requirement | Where |
| --- | --- |
| Parser versions and rollback | `parser/version.py`, `ops replay --rev` |
| Change detection, extraction drift | `storage/changes.py`, `quality/report.py` `drift` |
| Incremental runs | `--incremental` |
| Operational contract | `docs/OPERATIONS.md` |

## 12. Advanced

| Requirement | Where |
| --- | --- |
| API-first resolver | `ops assess`, `--discover`, JSON-LD and embedded data first |
| Link analysis, recrawl, hybrid search | `search/`. The vectors are hashed words and letter groups, not learned embeddings, so they forgive spelling but do not know synonyms |
| Agentic browsing | `interact` in the MCP server and the HTTP API |
| Watch API | `watch` in the MCP server and the HTTP API, `alerts.change_webhook` for pushes |
