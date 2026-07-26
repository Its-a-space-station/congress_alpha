# Task: M8 post-disclosure alpha decay + signal persistence (House × Tiingo)

## Context
- M7 built the full House corpus (23,889 PTR transactions, 1,660 Tiingo price
  series). M7's validation showed the aggregate filing-date signal is a coin
  flip (7,221 buys, 30d mean excess +0.1%, hit 47.8%). M8 answers the two
  questions that decide whether any strategy exists here at all: how much of
  the move happens before a filing becomes public (the invisible window), and
  whether past member outperformance persists out-of-sample. All local data —
  no new upstream sources. Executed as a goal-mode run.

## Plan
- [x] Design the four computations against the real schema, mirroring the M5b
      validation harness conventions (entry = first close on/after t0, exit =
      last close on/before horizon end, SPY excess, Wilson CIs, thin-sample
      flags).
- [x] Failing tests first: tests/test_analysis.py (9 tests, synthetic
      known-answer price grids, no network).
- [x] Implement app/intelligence/analysis.py + `analyze` CLI command
      (--provider tiingo|yahoo, default tiingo; strictly additive — no changes
      to existing commands).
- [x] Run `analyze` on the full corpus; write data/exports/
      alpha_decay_report.json + .md.
- [x] Gates green; this file rewritten as the M8 record.

## Notes — results (full corpus, as of 2026-07-26)
- Signals: 7,360 purchases with ticker + trade date + filing date (1,998 rows
  excluded for missing fields; 19 symbols with no Tiingo data, caveated).
- 1. Lag decay: invisible-window (trade → filing) mean excess is NEGATIVE in
  every lag bucket; copy-window (+30d) excess decays monotonically with lag:
  0-9d: n=437, invisible −0.12%, copy30 +1.01%
  10-19d: n=1,063, invisible −0.22%, copy30 +0.53%
  20-29d: n=1,958, invisible −0.12%, copy30 +0.24%
  30-44d: n=2,183, invisible −0.95%, copy30 −0.00%
  45+d: n=1,569, invisible −0.03%, copy30 −0.33%
  (mean lag 91.3d, median 30d — right-skewed by late amendments.)
  → The earlier a buy is disclosed, the better it does after disclosure; by
  the statutory 30–44d window the copy-trader's edge is zero. The aggregate
  invisible window is −0.4% (n=7,210) — members' entries do NOT beat SPY
  before disclosure either.
- 2. Entry vs copy edge: entry t0 30d −0.2% (hit 48%, n=7,216), entry 90d
  −0.3% (44%); copy t0 30d +0.1% (48%, n=7,221), copy 90d −0.1% (46%).
  → No aggregate edge on either side of the disclosure.
- 3. Walk-forward persistence (split 2021-01-01, n≥10 per period): only 8
  qualifying members (most heavy traders are newer members with no P1
  history); Spearman 0.17, top-decile retention 0%. Decays: Suozzi −1.08% →
  −6.85%, Foxx +1.09% → −0.79%, Wittman −0.21% → −1.73%; only Evans, Frankel,
  Sessions improved. → No evidence of persistent member skill; M7's
  track-record leaders do not reliably repeat.
- 4. Size weighting (entry-side 30d excess): 500k+ buys +1.09% (n=61),
  50k-100k +0.58% (n=226), 100k-250k +0.59% (n=106), 250k-500k −0.40% (n=24),
  ≤15k −0.20% (n=5,757), 15k-50k −0.20% (n=1,042). → The only positive
  gradient in the whole analysis: large conviction buys outperform token
  buys, but top-end samples are thin (61–226).
- Artifacts: data/exports/alpha_decay_report.json + alpha_decay_report.md
  (2026-07-26 00:24 UTC).

## Verification
- [x] Tests run — `uv run pytest`: 94 passed (9 new in tests/test_analysis.py).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 38 source files.
- [x] Manual verification completed —
  - `analyze` completed on the full corpus; all four analyses present in the
    JSON+MD report with real sample sizes (counts above).
  - New code is additive: existing ingest/parse/reconstruct/score/validate
    behavior untouched (suite green end-to-end).

## Review
- Summary of what changed:
  - New app/intelligence/analysis.py: TradeSignal collection, window/excess
    math, lag-decay bucketing, entry-vs-copy horizons, walk-forward
    persistence (Spearman + top-decile retention, stdlib-only), size buckets,
    JSON+MD report writer.
  - app/cli.py: `analyze` subcommand with --provider, summary logging.
  - tests/test_analysis.py: 9 known-answer tests (lag math, both t0
    conventions, negative-lag exclusion, bucket helpers, Spearman edge cases,
    persistence split, report writing).
- Honest reading of the findings:
  - Copy-trading House disclosures at filing date has no aggregate edge; the
    disclosure-lag gradient (fresh = better) is real but +1.01% on n=437 is
    thin and pre-cost.
  - Members' own entries are also ~flat vs SPY — the "politicians beat the
    market" premise is not visible in aggregate House data 2015-2026.
  - The size gradient (conviction sizing) is the most promising surviving
    signal; committee-overlap remains under-powered (278 signals in M7).
- Risks / follow-ups:
  - Persistence test is sample-starved (8 members); revisit with a lower
    min-n or rolling windows — documented, not silently widened.
  - Size-gradient should be tested for confounds (member fixed effects: a few
    wealthy active traders may drive it).
  - 19 symbols lack Tiingo data (delisted/renamed/mis-parsed tickers);
    retry alternates (SQ→XYZ) or a refresh.
  - 2012–2014 House docs remain unavailable; Senate remains blocked (M7).
  - Next candidate analyses: member fixed-effects on the size gradient,
    fresh-disclosure cohort deep-dive, per-ticker clustering.
