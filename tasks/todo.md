# Task: M6 proprietary composite v2 (policy-edge-0.2)

## Context
- First post-MVP milestone: the user's proprietary composite design — committee-
  realm overlap + relative position sizing + per-member track record, prices
  from their Tiingo membership. Executed as a goal-mode run.

## Plan
- [x] `.env` loader + secret resolution (env var > .env; key never logged).
- [x] Tiingo provider (`app/intelligence/tiingo.py`) + offline fixture.
- [x] `relative_size` component (max single-trade conservative ratio).
- [x] `track_record` component (per-member excess vs SPY, min-sample gated).
- [x] Config `policy-edge-0.2`; `validate --provider tiingo|yahoo`.
- [x] Golden tests incl. five-component decomposability.
- [x] Before/after demo + `validate` on Tiingo.
- [x] Update this file with verification details and review notes.

## Notes — component semantics (deterministic, documented in code)
- `relative_size`: max over the window (default 365d) of
  amount_min ÷ net_worth_max (conservative bounds both sides), scored
  weight × min(ratio ÷ 2% cap, 1). Max, not sum — one outsized bet is the
  signal; serial buyers are already covered by repeat_pattern. Zero + "no
  basis" without a parsed annual FD.
- `track_record`: per-member mean 30d excess vs SPY on purchases with filing-
  date t0 and a COMPLETED horizon before the scoring as-of; price series
  truncated to as-of (point-in-time). Gated: <5 signals → zero + "insufficient
  history". value = 20 × mean excess clamped to ±10%.
- Composite = exact sum of all five components (test-enforced); every
  component row carries a plain-text details string.
- Tiingo: token from TIINGO_API_KEY env or .env (gitignored); responses cached
  under data/raw/prices/; Yahoo kept as `--provider yahoo` fallback.

## Verification
- [x] Tests run — `uv run pytest`: 79 passed (Tiingo fixture parse, .env
      precedence, relative-size math + cap + no-basis, track-record gating/
      exact value/point-in-time exclusion, five-component decomposability;
      services tests updated for the v2 composite).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 37 source files.
- [x] Manual verification completed —
  - Before: v1 run 2 (policy-edge-0.1): {elevated: 1, low: 536}.
  - After: v2 run 3 (policy-edge-0.2): {low: 536, elevated: 1}; Rick Allen
    7.454 elevated with full decomposition — holdings 6.0 (E&C × AutoZone,
    Intuit), trading 1.5 (Church & Dwight), repeat 0, relative_size 0 ("no
    basis"), track_record **−0.046** ("n=6, mean 30d excess vs SPY −0.2%") —
    a real negative signal from real Tiingo prices, proving the engine is not
    a yes-man. Composite equals exact component sum.
  - `validate` (Tiingo default): 6 signals, 30d mean excess −0.2%, report
    rewritten from Tiingo data (consistent with the track-record number).

## Review
- Summary of what changed: tiingo.py provider, .env loader, two new score
  components, config policy-edge-0.2, validate --provider flag, 7 new tests
  (79 total).
- Risks / follow-ups:
  - relative_size and (to a lesser degree) track_record are data-hungry: most
    members currently lack parsed FDs/enough PTRs, so both read 0 with honest
    detail strings. They come alive as `refresh` grows the corpus — consider a
    full-corpus download run (or the cron line) before interpreting scores.
  - Track-record uses the 30d horizon only (single, completed-window); a 90d
    variant could be added to the config later.
  - Composite v2 makes the services/dashboard show five components everywhere
    automatically; no UI changes were needed.
  - The float-clamp boundary (excess a hair over +10%) is intentional and
    covered by a test assertion.
  - Senate remains blocked (M2a record); M6+ extensions (13F, USASpending,
    web migration) only if the user asks.
