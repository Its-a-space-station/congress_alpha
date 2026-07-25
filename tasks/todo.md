# Task: M5a Streamlit dashboard + service layer + CSV exports

## Context
- Fifth milestone, first half (per `tasks/plan.md` §3-M5 items 5.1–5.3): the
  user-facing dashboard. Executed as a goal-mode run. User design direction:
  dark background with neon accents. Historical validation + refresh job are M5b.

## Plan
- [x] Service layer (`app/intelligence/services.py`): watchlist (ranked, filters),
      member_detail (score breakdown, positions, net worth, transactions, notes),
      sector_clusters (read-model aggregation), export_rows.
- [x] Dark + neon theme (`.streamlit/config.toml`, committed): near-black
      `#0A0E14`, neon cyan `#00E5FF`, label colors per label band.
- [x] Streamlit app (`app/dashboard/app.py`): Watchlist / Member detail /
      Sector clusters views; explainability strings everywhere; notes add form.
- [x] CLI `export` → `data/exports/{watchlist,positions,transactions}.csv`.
- [x] Tests: service layer, export content, theme validity, AppTest smoke check.
- [x] Run gates + launch smoke check.
- [x] Update this file with verification details and review notes.

## Notes
- Architecture rule honored: the dashboard imports only service-layer functions
  (plus the notes service); no SQL or scoring logic in UI code — this is also
  what makes the UI testable headlessly.
- Watchlist rows come from the LATEST ScoreRun; sector filter matches the
  structured details strings ("sector via [...]") — documented in services.py.
- sector_clusters is a read model over memberships + positions + curated
  mappings (no re-scoring, no weights).
- Label badges use neon colors per band (low/moderate/elevated/high) with the
  neutral mission wording and a standing disclaimer caption on the watchlist.
- Launch command: `uv run streamlit run app/dashboard/app.py`.

## Verification
- [x] Tests run — `uv run pytest`: 65 passed (watchlist ordering + all filters,
      member detail assembly, sector cluster aggregation, export CSV round-trip,
      theme config validity, Streamlit AppTest: app executes with no exceptions).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 33 source files.
- [x] Manual verification completed —
  - Launch smoke check: headless `streamlit run` answered HTTP 200 on
    `/healthz` and `/`; clean startup log, graceful stop.
  - `export`: watchlist.csv 537 rows (Rick Allen top-ranked, 7.5 elevated,
    with decomposition), positions.csv 185, transactions.csv 32.
  - Visual/aesthetic review is the user's to make (per goal scope): open the
    app with `uv run streamlit run app/dashboard/app.py`.

## Review
- Summary of what changed: service layer + 6 tests (65 total), dashboard app
  with three views, dark+neon theme config, export CLI.
- Risks / follow-ups:
  - The dashboard reads the latest ScoreRun only; historical run comparison
    belongs to M5b validation.
  - Notes save immediately on submit (no edit/delete UI yet — CLI covers add/
    list; edit/delete could be M5b or a small follow-up).
  - Member-detail selector lists all scored members (537) — fine locally;
    add search if the corpus grows.
  - `streamlit` emits a usage-stats notice on first run; can be silenced via
    `browser.gatherUsageStats = false` in `.streamlit/config.toml` if desired.
  - Next: M5b (historical validation + daily refresh job) when the user asks.
