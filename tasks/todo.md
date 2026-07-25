# Task: M5b historical validation + daily refresh job (MVP complete)

## Context
- Final MVP milestone (per `tasks/plan.md` §3-M5 items 5.4–5.5 + paper upgrades).
  Executed as a goal-mode run. User authorized read-only price-data GETs;
  scheduler choice: `refresh` CLI + cron (CLAUDE.md allows "APScheduler or cron").

## Plan
- [x] Price provider (`app/intelligence/prices.py`) with raw caching + injection.
- [x] Validation harness (`app/intelligence/validation.py`): point-in-time PTR
      purchase signals vs SPY, decomposed metrics + report writer.
- [x] CLI `validate` → `data/exports/validation_report.{json,md}`.
- [x] Refresh job (`app/jobs/refresh.py` + CLI `refresh`): 9-stage pipeline,
      loud per-stage failure.
- [x] Cron/launchd documentation in README.md.
- [x] Tests: exact return math, Wilson CI, lookahead guard, thin-sample flag,
      refresh runner, report files.
- [x] Run gates + demo both commands.
- [x] Update this file with verification details and review notes.

## Notes — price provider incident (honest record)
- Original plan: stooq.com. On 2026-07-25 stooq served a JavaScript
  proof-of-work anti-bot challenge to plain GETs (both default and custom UAs).
  We do NOT circumvent bot challenges. Switched to Yahoo Finance's public v8
  chart endpoint (the JSON API Yahoo's own site calls; works with the
  congress-alpha UA, no challenge). Responses cached under `data/raw/prices/`;
  adjusted closes used when present. Provider is injectable, tests synthetic.
- This is exactly the "external source blocked → record and adapt" path from
  the goal's stop rule; the change is confined to `prices.py`.

## Notes — validation design (per plan's paper upgrades)
- Point-in-time discipline: t0 = FILING date (first public knowledge); entry =
  first close on/after t0; exit = last close on/before t0+horizon (30/90d);
  excess = stock return − SPY return over the same window; data-as-of recorded.
- Decomposed metrics only: counts, mean excess, hit rate with Wilson 95% CI,
  overlap/no-overlap cohorts, per-quarter breakdown, explicit thin-sample flag
  (<10). Scope label: "PTR purchase signal validation"; full score-history
  validation needs historical committee rosters (follow-up).
- First REAL result (bounded corpus, 2026-07-25): 6 signals — 30d mean excess
  −0.2% (hit rate 50%), 90d mean excess −0.1% (hit rate 33%), both flagged
  THIN SAMPLE. The harness correctly reports insufficient evidence rather than
  dressing up a tiny sample.

## Verification
- [x] Tests run — `uv run pytest`: 72 passed (exact forward-return math,
      point-in-time entry guard, Wilson CI values, thin-sample flags, signal
      collection + overlap cohorts, synthetic end-to-end, report files, refresh
      runner loud-failure).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 36 source files.
- [x] Manual verification completed —
  - `validate`: real prices for all corpus tickers + SPY; report files written
    with decomposed metrics + CI + thin-sample flag.
  - `refresh`: 9/9 stages (members → committees → filings → downloads →
    parse-ptr → parse-fd → reconstruct → score → export) with counters; one
    404 document handled gracefully (logged, skipped, batch continued).
  - Cron line verified documented in README.md.

## Review
- Summary of what changed: prices.py, validation.py, jobs/refresh.py, CLI
  `validate` + `refresh`, README sections (dashboard/refresh/validation + cron),
  7 new tests (72 total).
- Risks / follow-ups:
  - Corpus growth: the refresh run downloaded 100 more filings organically;
    parse coverage (and thus signal count for validation) grows with each run.
    Re-run `refresh` periodically (or set up the cron line).
  - Yahoo is an unofficial endpoint — if it ever bot-gates, do NOT circumvent;
    reassess providers (documented in prices.py).
  - Score-history validation (composite policy-edge vs returns) needs
    historical committee rosters — candidate for M6+.
  - Senate remains blocked (M2a record).
  - **MVP milestones M0–M5 are now complete.** Next steps would be M6+
    extensions (13F clustering, USASpending, web migration) only if the user asks.
