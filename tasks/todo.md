# Task: M2b House PTR parsing with golden-set QA

## Context
- Second half of M2 (per `tasks/plan.md` §3-M2 items 2.4–2.5): parse House Periodic
  Transaction Report PDFs into `Transaction`/`Asset` rows with verbatim raw text,
  deterministic parse confidence, and golden-set QA. Executed as a goal-mode run.
  Scope decision (user): PTRs only — annual-FD parsing is a future M2c.

## Plan
- [x] Add pdfplumber; extend downloader with a filing-type filter (`--type P`).
- [x] Download bounded real PTR corpus (15 PDFs ≤ 25 cap).
- [x] Inspect real PTR layout before designing the parser.
- [x] Build `app/parsing/house_ptr.py` (parser_version `house-ptr-0.1`).
- [x] Golden fixtures: 6 real PTR PDFs + agent-audited expected JSON (17 transactions)
      under `tests/fixtures/ptr/`.
- [x] Tests: golden exact-match (pass2pass), discrimination proof (fail2pass),
      negative controls, cross-checks, store idempotency/version-gating, asset dedupe.
- [x] Real `parse` CLI with per-filing + total parse stats.
- [x] Run gates + demo parse run.
- [x] Update this file with verification details and review notes.

## Notes — implementation findings (real PTR layout, 2026-07-24)
- PTR document URLs live under `ptr-pdfs/{year}/{DocID}.pdf` — NOT `financial-pdfs/`
  (corrects the M2a note; M2a's probe had only verified non-PTR types). `doc_pdf_url`
  is now type-aware.
- Table has no usable column ruling lines → text-mode regex parsing; `extract_tables`
  merges rows (verified).
- Real variations handled: owner codes (SP/JT/DC, blank=self per form convention),
  wrapped asset names, wrapped amount maxima ("$15,001 -" + "$50,000" on the
  continuation line), `S (partial)` partial sales (flagged in row warnings),
  treasuries with CUSIPs and `[GS]` codes (no ticker — never guessed), `[ST]`/`[OP]`
  asset-type brackets, identical duplicate rows in source (kept verbatim).
- Source data anomalies exist: filing 20026537 has notification dates (01/08/2024)
  BEFORE transaction dates (12/03/2024) — obvious source typos; parser stays verbatim,
  the `cross_check` function flags them. This is the designed division.
- `parse_confidence` is deterministic and decomposable (documented deductions in
  `_confidence`); midpoints stay null (M3).
- Model evolution: `Transaction.notification_date` added (STOCK Act timing analysis).
- Store idempotency: rows replaced only on parser_version bump; assets deduped by exact
  verbatim (name, ticker).

## Verification
- [x] Tests run — `uv run pytest`: 29 passed (6 golden exact-match, fail2pass
      discrimination, garbage/non-PTR negative controls, cross-check anomaly detection,
      store idempotency + version-bump replacement, verbatim asset dedupe).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 22 source files.
- [x] Manual verification completed —
  - Live `parse` run: 15/15 real PTRs parsed (goal required ≥10), 32 Transaction rows,
    per-filing stats logged; source date anomalies in 20026537 flagged by cross-checks.
  - Idempotency: immediate re-run → new=0, all 32 unchanged.
  - Spot check: GSK sale $1,001–$15,000 on 2025-07-28 → filing 20032062 → Robert
    Aderholt (A000055), confidence 1.0, parser house-ptr-0.1.
  - Row counts after demo: member 537, committee 49, membership 1,339, filing 4,047.

## Review
- Summary of what changed: real PTR parsing end-to-end — `app/parsing/` (house_ptr
  parser + versioned store), real `parse` CLI, type-filtered downloads, batch-resilient
  downloader (one bad doc no longer aborts), 7 fixture PDFs + 6 golden JSONs, 13 new
  tests.
- Risks / follow-ups:
  - Member matching gaps: nickname mismatch (`Richard` vs `Rick` Allen, GA12) leaves
    some current members unlinked — only 4/32 demo transactions are member-linked.
    Improve matcher (official_full/nickname fallback) before M4 scoring relies on it.
  - Decimal values render with trailing zeros from SQLite (cosmetic; compare numerically
    equal) — consider quantize at write time in M3.
  - "Over $X" open ranges and rare Cap-Gains column values are handled but untested by
    the current golden set — extend fixtures when such a filing appears.
  - Senate remains blocked (see M2a record).
  - Next: M2c (annual FD parsing — assets/liabilities for M3 net worth) or M3
    (normalization + reconstruction), when the user asks.
