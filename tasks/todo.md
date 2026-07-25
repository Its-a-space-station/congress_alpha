# Task: M2c annual FD assets/liabilities parsing + member-matching fix

## Context
- Final M2 slice: parse annual Financial Disclosure PDFs (assets + liabilities
  sections, per user scope decision) into new `Holding`/`Liability` tables, and fix
  the member-matching nickname gap flagged in M2b. Executed as a goal-mode run.

## Plan
- [x] Member-matching fix: `official_full` on Member, fallback rules (official-full
      tokens + curated nickname groups), fixture tests (Richard↔Rick), measured
      relink improvement.
- [x] Download bounded FD corpus (15 real annual FDs ≤ 25 cap).
- [x] Inspect real FD layouts before parser design.
- [x] Add `Holding`/`Liability` models.
- [x] Build `app/parsing/house_fd.py` (parser_version `house-fd-0.1`) + `store_fd.py`.
- [x] Golden fixtures: 6 real FDs + expected JSON (253 holdings, 19 liabilities).
- [x] Tests: golden exact-match, fail2pass, negative controls, cross-checks, store.
- [x] CLI: `parse --type A` dispatch by filing type.
- [x] Run gates + demo.
- [x] Update this file with verification details and review notes.

## Notes — member-matching fix (verified with real numbers)
- Root cause of the gap: index uses formal names ("Richard W. Allen"), dataset uses
  everyday names ("Rick"); `official_full` does NOT bridge it ("Rick W. Allen").
- Fix: curated nickname-group table (reviewable plain data in `house.py`) +
  official_full token fallback; exactly-one-candidate rule retained (ambiguity →
  unmatched). `Member.official_full` column added; members re-ingested from cache.
- Measured on the real index (`ingest filings --refresh`): 2025: 833 → 935 matched;
  2026: 283 → 303. Total 1,116 → 1,238 (+122, +10.9%), zero regressions.

## Notes — FD parsing findings (2026-07-24)
- Annual FDs are far harder than PTRs: account wrappers ("Fidelity IRA Rollover ⇒")
  with sub-assets, cells wrapping across lines in THREE different orders (columns,
  then name; name, then columns; mixed), L:/D: annotation lines, and at least two
  form variants (modern electronic; older two-income-column "Current/Preceding Year").
- Final architecture: coordinate-based column binning — each section's own header
  line provides x anchors; words are binned to true columns; rows assemble via
  bracket `[XX]` / "⇒" anchors with a completion rule (bracket + value-or-"None").
  Two earlier text-mode attempts failed on real variants (freeze rule applied:
  switched strategy instead of patching a 3rd time).
- Section headers can appear MID-PAGE on candidate filings → document-order
  (header, body_line) streams per section; earned-income/transactions/positions/
  agreements/travel sections detected and skipped.
- "None" is a legitimate disclosed value (no completion without it); source footer
  and spaced-banner lines filtered by vocabulary/banner rules.
- Cross-checks: value min ≤ max on holdings and liabilities; "not an FD" detection;
  garbage PDFs → zero rows + warning, never a crash.
- Results on 15 real FDs: 875 holdings + 56 liabilities, only ~1% low-confidence
  rows (all flagged, none silent).

## Verification
- [x] Tests run — `uv run pytest`: 42 passed (6 FD golden exact-match incl. 167-row
      Bennett filing, fail2pass, garbage/PTR negative controls, cross-check anomaly,
      store idempotency + version-bump replacement, verbatim asset dedupe; plus the
      new nickname/ambiguity matcher tests).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 24 source files.
- [x] Manual verification completed —
  - Live `parse --type A`: 15/15 FDs parsed (goal required ≥10), 931 rows;
    per-filing stats logged. Idempotent re-run → all 931 unchanged.
  - Live `parse` (PTR regression): still 15/15, 32 transactions.
  - Relink measurement: +122 member-linked filings over the 1,116 baseline.
  - Spot checks: IVV holding ($50,001–$100,000, income verbatim) → Bacon filing
    (member-linked, parser house-fd-0.1); Freedom Mortgage liability row correct.

## Review
- Summary of what changed: `app/parsing/house_fd.py` + `store_fd.py`; `Holding` and
  `Liability` tables; matcher fallback chain + nickname groups + `Member.official_full`;
  `parse --type A` CLI dispatch; 6 FD fixtures + golden JSON; 13 new tests (42 total).
- Risks / follow-ups:
  - FD form variants beyond the two seen (hand-written/scanned filings) are
    unhandled — they will surface as low-confidence/zero-row parses when the corpus
    grows; extend golden fixtures as they appear.
  - `income_details` is verbatim text (per goal scope); structuring income types/
    amounts belongs to M3 if net worth needs them numerically.
  - Member matching is still name-based (no bioguide in the House index); ambiguous
    same-name cases stay unmatched by design. Match rate should be re-measured after
    corpus growth.
  - The transactions section (Schedule B) is skipped per scope decision; if M3 finds
    members with missing PTRs, revisit as M2d.
  - Senate remains blocked (see M2a record).
  - Next: M3 (normalization + position reconstruction + net worth) when the user asks.
