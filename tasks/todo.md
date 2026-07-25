# Task: M4 policy-edge scoring engine + notes engine

## Context
- Fourth milestone per `tasks/plan.md` §3-M4: a deterministic, decomposable
  committee-overlap ("policy-edge") score plus a manual notes engine. Executed
  as a goal-mode run. No network needed.

## Plan
- [x] Curated versioned mapping files (`mappings/committee_sectors.yaml`,
      `mappings/ticker_sectors.yaml`) — user scope decision: curated seeds only,
      unmapped = no contribution.
- [x] Mappings loader with version tracking (`app/intelligence/mappings.py`).
- [x] Scoring engine + typed config (`app/intelligence/scoring.py`).
- [x] Notes service (`app/intelligence/notes.py`).
- [x] CLI: real `score`; `note add` / `note list`.
- [x] Golden scoring tests incl. decomposability + notes CRUD.
- [x] Run gates + demo on the real DB.
- [x] Update this file with verification details and review notes.

## Notes — scoring design (per CLAUDE.md and the plan's paper upgrades)
- Three first-class components, each a ScoreComponent row with a human-readable
  details string naming its contributors: `committee_sector_holdings_overlap`
  (2.0/sector + 1.0/asset), `committee_sector_trading_overlap` (same weights,
  scaled by average trade recency: full ≤90d, half ≤365d, ignored beyond),
  `repeat_pattern` (flat 2.0 at ≥3 overlapping trades). Composite = exact sum;
  tests assert the equality and the contributor strings.
- All knobs in `ScoringConfig` (`CONFIG_VERSION = policy-edge-0.1`); the ScoreRun
  records config + mapping versions (committee@1.0+ticker@1.0) + parser versions.
  No tuning without a version bump; no evolutionary search (plan rule).
- Mapping curation rules (documented in the files): only unambiguous jurisdictions
  (broad committees like Appropriations/Ways and Means/Intelligence unmapped);
  single sector per ticker; broad-market ETFs unmapped; sector ETFs mapped.
- Label bands: LOW < 3 ≤ MODERATE < 6 ≤ ELEVATED < 10 ≤ HIGH. Neutral wording
  per the mission guardrails.

## Verification
- [x] Tests run — `uv run pytest`: 59 passed (golden scenario with exact component
      values and decomposition strings, repeat threshold, recency decay-out,
      band boundaries, run determinism, mappings loader, notes CRUD + validation).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 31 source files.
- [x] Manual verification completed —
  - `score` on the real DB: run 1 with label distribution {low: 536, elevated: 1};
    top overlap = Rick Allen composite 7.5 (elevated), decomposition showing
    HSIF (Energy & Commerce) holdings overlap via AutoZone (consumer) and Intuit
    (technology) — matches his real committee assignment.
  - `note add` / `note list` round-trip by member, by filing, and member+filing.
  - Conservatism is structural: members without parsed filings or mapped
    committees/assets score 0 (LOW).

## Review
- Summary of what changed: mappings/ seeds + loader, scoring engine + config,
  notes service, real `score` + `note` CLI, 7 new tests (59 total).
- Risks / follow-ups:
  - Score coverage is corpus-bound: members without parsed filings score 0.
    Interpretation of LOW must include "no data" — consider an explicit
    data-coverage field on the composite row in a future iteration.
  - Mapping seeds are v1.0 and will need periodic review as the corpus grows
    (new tickers, committee roster changes); bump mapping_version each time.
  - Committee/company (exact-name) overlap is currently approximated by the
    ticker map; a dedicated company-jurisdiction curation could be M4.1 if the
    user wants finer granularity.
  - Senate remains out of scope (blocked in M2a); Senate committees exist in the
    DB and are mapped, ready when Senate data unblocks.
  - Next: M5 (dashboard + historical validation) when the user asks.
