# Task: M1 member + committee ingestion

## Context
- First real ingestion milestone (Phase 1) per `tasks/plan.md` §3-M1: populate Member,
  Committee, and CommitteeMembership from the official `unitedstates/congress-legislators`
  YAML datasets, with a re-runnable staged ETL and idempotent upserts.
- Executed as a goal-mode run; the user waived the pre-implementation plan review for this
  milestone (record-keeping via this file still applies).

## Plan
- [x] Verify dataset structure and URLs; document findings.
- [x] Snapshot raw YAML into `data/raw/congress_legislators/` with a provenance manifest
      (source URL, sha256, bytes, fetch time per file in `manifest.json`).
- [x] Build `app/ingestion/http.py` — httpx fetch with retry/backoff and on-disk raw cache.
- [x] Build staged ETL: snapshot (`sources.py`) → parse/validate into typed records
      (`records.py`) → idempotent upserts (`loaders.py`) → row-count validation
      (`table_counts`).
- [x] Wire CLI: `ingest members` / `ingest committees` (both with `--refresh`).
- [x] Standard counters logged per run (total/new/changed/unchanged/skipped).
- [x] Fixture tests: parse validation, upsert idempotency + change counting, membership
      linking, row counts, cache/manifest behavior.
- [x] Run gates and demo the CLI on the local DB.
- [x] Update this file with verification details and review notes.

## Notes
- Dataset verification (2026-07-24, all HTTP 200 via raw.githubusercontent.com):
  - `legislators-current.yaml` (~1.08 MB): list of legislators; `id.bioguide` stable key;
    `terms` list where the LAST term is current (dataset convention); term `type` is
    `rep`/`sen`; `district` present for most House terms, absent for at-large/delegates.
  - `committees-current.yaml` (~63 KB): list; `thomas_id` code, `name` verbatim, `type` is
    `house`/`senate`/`joint`.
  - `committee-membership-current.yaml` (~292 KB): mapping of committee code → member list
    with `bioguide`; ALSO contains subcommittee keys (parent code + number) — out of M1
    scope, skipped by exact-match against full-committee codes.
- Model change vs M0: added `Chamber.JOINT` (joint committees exist in the dataset;
  members are always house/senate).
- Added deps (M1 need): `pyyaml`, dev `types-pyyaml`.
- Live run results (2026-07-24): 537 members, 49 full committees, 1,339 committee
  memberships ingested; 181 membership keys/entries skipped (subcommittee keys +
  bioguides not in the members dataset, e.g. non-congressional joint-committee members).
- Ingestion order matters: `ingest members` before `ingest committees` (memberships link
  by bioguide lookup).

## Verification
- [x] Tests run — `uv run pytest`: 11 passed (parse validation, idempotent upserts,
      change counting, membership linking incl. unknown-bioguide skips, row counts,
      cache-hit-without-network, snapshot manifest).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 18 source files.
- [x] Manual verification completed —
  - Live: `ingest members` → total=537 new=537; `ingest committees` → 49 new,
    memberships 1,339 new / 181 skipped; row counts confirmed in SQLite.
  - Idempotency: immediate re-runs → new=0, all unchanged (members 537, committees 49,
    memberships 1,339).
  - Cache: second run logged `cache hit` for all three snapshots; no network refetch.
  - Spot checks: Maria Cantwell (senate, Democrat, WA, 2025-01-03..2031-01-03) and HSAG
    House Committee on Agriculture read back correctly.

## Review
- Summary of what changed: new `app/ingestion/` package (http, sources, records, loaders);
  CLI `ingest` now real with `members`/`committees` targets; `Chamber.JOINT` added;
  `pyyaml` + `types-pyyaml` deps; 3 YAML fixtures + 2 new test files; raw snapshot with
  provenance manifest under `data/raw/congress_legislators/`.
- Risks / follow-ups:
  - Subcommittees are skipped in M1; M4 scoring may want subcommittee granularity — would
    need a `parent_committee_id` on Committee and parse of subcommittee membership keys.
  - Membership removals are not propagated (stale links are kept, not deleted) —
    conservative for now; revisit if roster churn matters for scoring.
  - `legislators-historical.yaml` exists upstream and is NOT ingested (MVP scope: current
    members only); would be needed for M5 historical validation.
  - Delegates/resident commissioners are ingested as House members with `district` null or
    "0"-style values as provided; scoring can filter later if desired.
  - Next milestone per plan: M2 (disclosure ingestion & parsing — House/Senate portals),
    only when the user asks.
