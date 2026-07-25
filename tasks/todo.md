# Task: M3 normalization, position reconstruction, net-worth estimation

## Context
- Third milestone per `tasks/plan.md` §3-M3: turn parsed rows into derived,
  certainty-labeled estimates — estimated open positions and household net
  worth. Executed as a goal-mode run. No network needed (computes entirely
  from the local DB).

## Plan
- [x] Normalization: documented `midpoint()` utility; asset canonicalization
      with grouping confidence (ticker=high, canonical name=medium, ungrouped=low).
- [x] Input-validation checkpoint (`app/intelligence/checkpoint.py`) — per-member
      data facts feeding every certainty label.
- [x] Position reconstruction (`app/intelligence/positions.py`) — documented
      conservative method.
- [x] Net-worth estimator (`app/intelligence/net_worth.py`) — household sums with
      conservative bounds.
- [x] CLI `reconstruct`: midpoint backfill + deterministic rebuild + stats.
- [x] Golden scenario tests (baseline, purchase, full/partial sale, range-only,
      missing baseline) + normalization + net worth + rebuild determinism.
- [x] Run gates + demo on the real DB.
- [x] Update this file with verification details and review notes.

## Notes — method decisions (per CLAUDE.md auditability rules)
- Reconstruction method (in `positions.py` docstring): baseline = latest annual-FD
  holdings (MEDIUM certainty — disclosed presence, ranged aging values); PTR overlay
  = post-baseline purchases open LOW positions, purchases of baseline assets
  corroborate (MEDIUM), post-baseline sales drop the position to LOW with a
  "may be reduced/closed" method string ("[partial sale noted]" for S-partial rows);
  sales of never-observed assets are skipped as unobservable (14 in the demo).
- Ranged arithmetic only; open-range (max-less) purchases record presence without
  a dishonest upper bound; midpoints are display-only via `normalize.midpoint`.
- Net worth: household = FD household definition (self+spouse+joint+dependent);
  estimate_min = Σ holding mins − Σ liability maxes, estimate_max = Σ holding
  maxes − Σ liability mins; disclosed-None holdings count as zero. Certainty from
  checkpoint completeness (HIGH/MEDIUM/LOW); no estimate without a parsed annual FD.
- Derived rows (Position, NetWorthEstimate) are rebuilt deterministically each run
  — always recomputable from parsed rows.

## Verification
- [x] Tests run — `uv run pytest`: 52 passed (10 new M3 tests covering all six
      golden scenarios, midpoint math, grouping confidence, household net-worth
      sums, HIGH certainty assignment, deterministic rebuild).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 28 source files.
- [x] Manual verification completed —
  - `reconstruct` on the real DB: 32 midpoints backfilled; 185 positions
    (167 MEDIUM, 18 LOW) across 537 members; net worth for 11 members
    (10 HIGH, 1 MEDIUM) — the 11 = distinct members in the bounded parsed-FD
    corpus; 14 unobservable sales skipped.
  - Spot checks: Bacon net worth $97,035–$2,154,999 (HIGH, year 2025) consistent
    with his holdings minus mortgage; Rick Allen (PTR-only, no parsed FD) → 18
    LOW positions with honest no-baseline method strings.
  - Determinism: second run rebuilds identical derived rows.

## Review
- Summary of what changed: new `app/intelligence/` package (normalize, checkpoint,
  positions, net_worth); `reconstruct` CLI; 10 golden scenario tests.
- Risks / follow-ups:
  - Positions/net worth cover only members with parsed filings (bounded corpus);
    coverage grows with full-corpus downloads (run `ingest downloads --limit N`
    and re-parse) — the machinery is corpus-size-independent.
  - Treasury/CUSIP assets group by canonical name (no ticker in source); if M4
    scoring needs security-level grouping, revisit with a curated map (never
    silent).
  - `income_details` on holdings stays verbatim; not used numerically anywhere.
  - Baseline uses the LATEST FD only; multi-year FD history (for M5 point-in-time
    validation) will need per-year baselines — the engine already takes an as-of
    date for this purpose.
  - Next: M4 (scoring + comments engine) when the user asks.
