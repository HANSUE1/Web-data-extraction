# Operational contract and runbook

A living document. Review it when a target site, the schema or the business need changes, and at least
once a year. `python -m my_scraper.ops gate` fails when one of the contract sections is missing.

Lines marked **TODO** need an answer from whoever runs this.

# Contract

## Scope

- Public pages of the sites in the site register (`compliance.site_register`). Nothing else.
- Out of scope, by design: CAPTCHA solving, stealth or disguise, paywalls, logins without permission.
- A new site needs a register entry and `python -m my_scraper.ops assess <url>` answering `go`.
- **TODO**: list the sites and the business purpose of each.

## Freshness

- Set by `config/schedules.json`. The scheduler starts a run it missed by up to one hour.
- `every: auto` follows how often the page has been changing (1 hour to 30 days).
- **TODO**: the oldest the data may be before it is no use.

## Correctness

- Every record passes `quality.schema`. Those that don't go to `rejected.jsonl` with the reason.
- A dataset below `min_items`, `min_valid_ratio` or above `max_null_ratio` is quarantined, not published.
- A field or page that yields much less than in the last five runs raises a `drift` alert.
- The parser is tested against `my_scraper/tests/fixtures` and by replaying stored snapshots.

## Failure policy

| What happens | What the scraper does |
| --- | --- |
| Timeout, 5xx, 429 | Retries with backoff, obeying `Retry-After`. Then the URL goes to the dead-letter file |
| 404 and other permanent errors | Recorded, not retried |
| Block page, login wall, consent wall | Stops. The job becomes a dead letter; nothing is worked around |
| Several refusals in a row, or a high error rate | The site's circuit opens. The job is tried again after an hour |
| The page does not parse | The snapshot is kept and parsed again after a fix. The site is not asked again |
| A worker dies | Its lease runs out and another worker resumes from the checkpoint |
| Attempts used up | Dead letter, and an alert |

## Delivery

- Files in `outputs/results/<site>_<time>/`, described by `outputs/state/manifests/<same name>.json`
  (counts, quality, schema and parser version, SHA-256 of each file).
- Database table `storage.table`, in one transaction, as `snapshot`, `append` or `upsert`.
- Changes are pushed to `alerts.change_webhook` when `track_changes` is on.
- **TODO**: who consumes the data, and who to tell before the schema changes.

## Access review

- Secrets are only ever `env:NAME` or `file:/path` references. `ops gate` scans the repository for leaked ones.
- `SCRAPER_API_KEY` guards the API, the dashboard and the metrics. Only `/health` is open.
- Snapshots hold pages as they were, personal data included, even with `compliance.redact_pii`, which covers
  records. Restrict access to `outputs/` and the bucket, and set `storage.retention_days`.
- **TODO**: who has access, and the date of the last review.

## Ownership

- **TODO**: owner, on-call contact, and who may approve a site in the register.

# Runbook

```bash
python -m my_scraper.ops health --config config/books.json --schedules config/schedules.json
python -m my_scraper.ops jobs --state dead
```

| Alert | First steps |
| --- | --- |
| `success_rate` | Dashboard, recent errors. One site or all? All: network, proxy (`ops proxies`). One: it may be down or refusing |
| `drift:*` | Look at the newest snapshot. Fix the selectors, then `ops replay --host <site>` to see what the fix changes |
| `quality:*` | `outputs/results/quarantine/`, and `rejected.jsonl` in it. Fix, then run again |
| `dead_letters` | `ops jobs --state dead`. Refused: leave it, and review the site. Otherwise fix the cause and `ops jobs --retry <id>` |
| `export:*` | Disk space, database connection. The run's records are in the job's checkpoint and are delivered on retry |
| `queue_depth` | Add workers, or drop the priority or frequency of the largest schedule |

**Release a parser change.** `ops replay --latest --strict` on real snapshots, review the differences, bump
`PARSER_RELEASE` in `my_scraper/parser/version.py`, update the fixtures in a new folder if the change is meant.

**Roll a parser back.** `ops replay --rev <commit> --export old.jsonl` parses history with the old code. Reverting
the commit puts it back in service.

**Takedown request.** `ComplianceManager.handle_takedown_request(domain, reason)` blocks the domain from every
later crawl. Delete its snapshots and records as the request asks.

**Recover after losing a machine.** The queue is in Redis (append-only file) or `outputs/state/queue.db`, snapshots
are in S3 or `outputs/state`. Restore those, start a worker, and running jobs resume when their leases run out.
