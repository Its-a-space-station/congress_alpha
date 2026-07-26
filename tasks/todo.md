# Task: M7 full-corpus backfill 2012–present + Senate spike

## Context
- M6's review flagged that the v2 composite was data-starved (relative_size /
  track_record read 0 for most members on the thin corpus). M7 backfills the
  full House corpus 2012–present and runs a bounded Senate unblocking spike.
  Executed as a goal-mode run.

## Plan
- [x] Preflight: disk 320 GiB free (>=10 required); House index availability
      verified HTTP 200 for every year 2012–2026.
- [x] House index: `ingest filings` for all years 2012–2026; per-year row
      counts logged (see Notes).
- [x] Early-scheme handling: `Filing.disclosure_type` column; O+PTR mapped to
      PTR; 2012–2014 documents verified 404 at current public_disc paths and
      excluded from downloads with a logged skip count.
- [x] House downloads: P+A pending queue drained (paced, resumable, chunked
      background batches); failures logged and categorized (see Notes).
- [x] House parse: `parse --type P` and `parse --type A` over the full
      downloaded corpus; per-type stats logged (see Notes).
- [x] Rescore + validate: `reconstruct`, `score` (policy-edge-0.2, Tiingo),
      `validate` on the full corpus; new distribution + validation report.
- [x] Senate spike (bounded): eFD session flow attempted with a persistent
      httpx session — BLOCKED at the Akamai edge; evidence recorded.
- [x] This file rewritten as the M7 record.

## Notes — per-stage counts
- House index rows by year (index_rows | raw P | raw A | downloaded all types):
  2012: 5347 | 0 | 737 | 0        2015: 2293 | 728 | 213 | 941
  2013: 7337 | 0 | 800 | 0        2016: 2647 | 765 | 230 | 995
  2014:   11 | 0 |   8 | 0        2017: 3428 | 801 | 256 | 1057
                                 2018: 3228 | 830 | 238 | 1068
  2019: 3578 | 683 | 202 | 885    2023: 2372 | 460 | 129 | 589
  2020: 2895 | 733 | 161 | 894    2024: 2247 | 451 | 103 | 554
  2021: 2717 | 680 | 164 | 844    2025: 2653 | 515 |  93 | 706
  2022: 2741 | 624 | 180 | 804    2026: 1394 | 313 |  33 | 346
  TOTAL index rows 2012–2026: 44,888.
- Early scheme (2012–2014): 2012–2013 PTRs are coded filing_type_raw=O +
  disclosure_type=PTR (3,131 total: 788 in 2012, 2,146 in 2013, plus small
  A-coded PTR counts); early FDs sprawl across raw codes A/D/E/F/G/N/O/R/T/W/X.
  ALL 2012–2014 documents 404 at the current public_disc paths (7 probes, 100%
  consistent) — source reality, not a bug. 2014 is a genuine stub year (11
  index rows, no PTRs). Retrievable corpus is therefore 2015–2026:
  7,583 P + 2,002 A = 9,585 docs. The 1,545 pre-2015 A-coded filings and
  35,205 non-P/A raw-type rows were excluded by design.
- Downloads: 9,585/9,585 fetched; pending P+A queue empty (0 remaining);
  failures: 0 (no 404s, no network failures across the whole drain).
- Parse stats:
  - PTR: 2,730/7,583 filings yielded rows; 23,889 transaction rows (23,848
    new this run); low-confidence (<0.5) share 8.0% (1,904 rows); 4,476 doc
    lines with warnings.
  - Annual FD: 709/2,002 filings yielded rows; holdings 28,406 rows across
    687 filings (low-confidence 2.5%); liabilities 1,598 rows across 534
    filings (low-confidence 0.0%); 1,317 doc lines with warnings.
- Reconstruct: midpoints backfilled on 23,468 transactions; 8,963 positions
  ({'low': 3,865, 'medium': 5,098}) for 537 members; net worth for 280 members
  ({'high': 248, 'medium': 26, 'low': 6}); 3,607 unobservable sales skipped.
- Score run 4 (policy-edge-0.2, Tiingo): label distribution
  {'low': 502, 'moderate': 16, 'elevated': 8, 'high': 11} across 537 members
  (M6 baseline, run 3: {'low': 536, 'elevated': 1}). Top decompositions:
  John James 28.0 (high), Thomas Kean 22.9, Greg Landsman 22.6, Rick Allen
  19.5, Lisa McClain 15.7 — all driven by committee_sector_holdings_overlap,
  i.e. the new annual-FD holdings corpus doing real work.
- Validate (Tiingo, real sample sizes): 30d: 7,221 signals, mean excess +0.1%,
  hit rate 48%; 90d: 7,223 signals, mean excess −0.1%, hit rate 46% (M6
  baseline: 6 signals, 30d mean −0.2%). Reports: data/exports/
  validation_report.json + validation_report.md (2026-07-25 21:59).
  10 tickers returned empty Tiingo series (HTTP 200, body `[]`) and are
  recorded in the report caveats: AMI, ATUS, BHLB, CCE, MMC, MPW, PSTG, SQ,
  TFM, TYC.

## Notes — Senate spike outcome: BLOCKED (documented blocker with evidence)
- Bounded spike: persistent httpx session, full browser headers; stop rule =
  no headless browser, no challenge solving.
- Five session attempts (landing /search/, /search/home/, root, index.html),
  all HTTP 403 at the Akamai edge (AkamaiGHost) before any application
  content — the session-level acknowledgment and search API were never
  reachable from this client.
- Evidence: /tmp/senate_landing.html (Access Denied page, Reference
  #18.d1403617.1784946311.1fdd408e via errors.edgesuite.net); live
  re-confirmation 2026-07-25T23:12:05Z -> HTTP 403, Reference
  #18.d1403617.1785021125.250c8755. Full write-up: /tmp/m7_senate_evidence.txt.
- Follow-up (out of M7 scope): Senate access needs an official bulk source,
  a different network origin, or a sanctioned API.

## Verification
- [x] Tests run — `uv run pytest`: 85 passed.
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 37 source files.
- [x] Manual verification completed —
  - House index rows exist for every year 2012–2026 (counts above).
  - P+A pending-download queue empty (0 pending); 9,585/9,585 fetched, 0
    failures (categorized set is empty).
  - Full-corpus parse stats logged above.
  - `score` run 4 (policy-edge-0.2) and `validate` completed on the full
    corpus; distribution and report above (compare M6: low 536 / elevated 1,
    6 validation signals).
  - Senate spike outcome recorded above (documented blocker with evidence).

## Review
- Summary of what changed:
  - Pipeline: full House index 2012–2026 (44,888 rows); 9,585 P/A documents
    downloaded; full-corpus parse (23,889 PTR transactions, 28,406 holdings,
    1,598 liabilities); reconstruct; score run 4; validation report with
    ~7.2k signals per horizon.
  - Schema: `Filing.disclosure_type` column for the 2012–2014 early scheme.
  - `app/ingestion/http.py`: `redact_credentials()` — token/api-key query
    params masked in every log line and in the raised fetch error (previously
    the Tiingo key was logged on every request).
  - `app/intelligence/tiingo.py`: persistent fetch failure (e.g. delisted
    ticker 404) now logs a warning and returns {} instead of raising —
    one dead ticker no longer kills a full score/validate run.
  - `app/core/logging.py`: httpx/httpcore pinned to WARNING (their INFO
    request lines carry full credential-bearing URLs).
  - Tests: +2 fetch-redaction tests (test_ingestion_http.py), +1
    empty-series-on-404 test (test_scoring_v2.py), +1 logging test
    (tests/test_logging.py); suite now 85 tests.
  - Ops: 1,045 leaked-key occurrences scrubbed from /tmp/m7_score.log after
    the logging fix landed; validate log verified clean (0 occurrences).
- Risks / follow-ups:
  - 2012–2014 documents are unavailable at the current public_disc paths;
    recovering them would need an alternate official archive.
  - Senate remains blocked pending an access-path decision.
  - 10 tickers with empty Tiingo series (listed above) — retry with alternate
    symbols (e.g. SQ -> XYZ) or a refresh; they are caveated in the report.
  - The Tiingo key appeared in the session wire log (outside the project
    root) via a log tail before the redaction fixes; rotating the key is
    recommended hygiene.
  - Validation is still filing-date t0 vs SPY on PTR purchases only; full
    score-history validation needs historical committee rosters.
