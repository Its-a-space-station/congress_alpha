# Congress Alpha — Overall Execution Plan (v2, paper-informed)

Provenance: v1 approved 2026-07-24 (milestone plan M0–M6+). This v2 integrates a
synthesis of the user's "Papers on Coding" folder (39 PDFs reviewed 2026-07-24;
see §8 for the relevance index, including four mislabeled filenames). Upgrades are
marked **[paper: X]** so every change is traceable to a source.

## 0. Objective (unchanged)

Local-first, auditable research engine: ingest official House/Senate financial
disclosures → reconstruct estimated open positions and household net worth (with
certainty labels) → conservative, deterministic, decomposable committee-overlap
"policy-edge" score → ranked watchlists in Streamlit. No accusatory language.
Local-first MVP (M0–M5), then optional M6+ extensions.

## 1. Status

- **M0 scaffold: DONE** (2026-07-24). Gates green: `uv run pytest` (6), `ruff`, `mypy`.
- Stack: Python 3.12 (uv-pinned), SQLite + SQLModel, Streamlit, pytest, ruff, mypy.
- Data sources (verified): congress-legislators YAML (members/committees),
  disclosures-clerk.house.gov (XML index + PTR PDFs), efdsearch.senate.gov
  (session/CSRF + HTML PTR tables).

## 2. Architecture (unchanged)

```
sources → ingestion → data/raw (immutable) → parsing → db (raw_text + confidence
+ parser_version) → intelligence (positions, net worth) → scoring (decomposable)
→ services → dashboard (no logic) → data/exports
```

## 3. Milestones

### M0 — Scaffold ✅ DONE

### M1 — Member + committee ingestion

Sub-milestones per v1 (dataset confirmation, polite httpx client, idempotent
upserts, fixture tests). Upgrades:

- Stage the ETL explicitly — raw snapshot → deterministic filters → load →
  row-count validation — each stage independently re-runnable and logged.
  **[paper: SWE-rebench]**
- Emit the same standard counters on every run (member counts, new/changed/removed)
  so regressions across data drops are visible. **[paper: Scaling Laws]**

### M2 — Disclosure ingestion & parsing (highest risk)

Sub-milestones per v1 (portal verification, filing index, downloader, House
pdfplumber + Senate HTML parsers, golden QA set). Upgrades:

- **Fixed-stage pipeline with per-stage gates**: download → text extraction →
  row parsing → validation → human-review queue. No long autonomous parsing
  sessions. **[paper: SWE-Gym]**
- **Oracle-first**: build a small set of filings with known-correct expected
  parses *before* scaling parser work; the oracle drives development.
  **[paper: SWE-smith]**
- **Golden-set acceptance rule (fail2pass / pass2pass)**: a parser change lands
  only if filings that parsed before still parse identically AND previously
  failing ones now parse. **[papers: SWE-rebench, SWE-Bench Pro]**
- **Negative controls**: an empty/garbage filing must yield zero records (a
  no-op parser must FAIL the fixtures). **[paper: Terminal-Bench]**
- **Deterministic extraction cross-checks** for the three classic table failure
  modes: adjacent-cell misreads, wrong metric (amount range vs exact), wrong
  time granularity (fiscal period confusion) — plus owner-column checks.
  **[paper: EvoSkill]**
- **Independent validation pass** post-parse (row counts, date sanity,
  cross-foot totals where the filing provides them) routing low-confidence
  parses to review; feeds `parse_confidence`. Verifier output informs but never
  auto-trusted. **[paper: SWE-Gym]**
- **Coverage over perfection**: ingest a diverse spread of chambers/filing
  types/years early rather than perfecting one source. **[paper: SWE-smith]**
- Inspect real artifacts before designing abstractions; build one-off inspector
  scripts as needs arise. **[papers: Live-SWE-Agent, Overthinking]**

### M3 — Normalization & position reconstruction

Sub-milestones per v1 (asset/ticker confidence mapping, amount ranges →
min/max/midpoint, chronological reconstruction, net-worth estimator). Upgrades:

- **Mandatory input-validation checkpoint before computation** (ranges, dates,
  owner types sane); the certainty label is emitted *at* that checkpoint, not as
  an afterthought. **[paper: EvoSkill]**
- No automated tuning of estimator heuristics — net worth has no ground-truth
  oracle, so human-auditable heuristics + certainty labels are the ceiling.
  **[paper: AlphaEvolve]**
- Fix and state the aggregation granularity (per-filing vs per-quarter) before
  computing certainty labels; results depend on that choice.
  **[paper: Complexity/Coffee Automaton]**

### M4 — Scoring + comments engine

Sub-milestones per v1 (versioned committee→sector map, deterministic overlap
components, repeat-pattern detection, composite score, notes CRUD). Upgrades:

- **Sub-scores are first-class outputs** (committee/company overlap,
  committee/sector overlap, repeat-pattern, recency) — do not collapse to one
  scalar early; decomposition measurably improves the whole.
  **[papers: AlphaEvolve, GLM-5]**
- **No evolutionary/LLM search over scoring weights** — violates the
  deterministic-auditable rule and would overfit thin historical data. Scoring
  changes are human-reviewed structural changes, not threshold nudging.
  **[papers: DGM, AlphaEvolve, Bilevel Autoresearch]**
- Simplicity review test for every new rule: its description cost must be
  outweighed by the reduction in unexplained residual (fewer low-confidence
  records); otherwise reject it. Per-filing special-case hacks = overfitting.
  **[papers: MDL, Hinton & van Camp]**

### M5 — Dashboard + historical validation

Sub-milestones per v1 (service layer, Streamlit app, CSV exports, validation
hooks, daily refresh job). Upgrades:

- **Point-in-time discipline as decontamination**: the scoring engine must be
  structurally incapable of using disclosures or prices dated after the as-of
  date; record data-as-of dates; snapshot input data so backtests are
  byte-reproducible; flag ambiguous windows rather than silently trusting them.
  **[papers: SWE-rebench, Terminal-Bench]**
- **Decomposed validation metrics**, reported separately: parse coverage,
  position-reconstruction accuracy, net-worth error bands, score→return hit
  rates with confidence intervals and per-period breakdowns — never one
  headline number. **[papers: GLM-5, SWE-rebench]**
- **Evaluation cascade**: cheap sanity checks (row counts, amount consistency,
  no-negative-shares) across all filings before any full backtest.
  **[paper: AlphaEvolve]**
- Measure parse-confidence distributions across the whole corpus first; let
  measured failure modes — not intuition — pick follow-up work.
  **[paper: Scaling Laws]**

### M6+ — Post-MVP (only on explicit request)

13F clustering, USASpending, hiring-surge, web migration (FastAPI + hosted DB).

## 4. Workflow upgrades (how the agent builds — CLAUDE.md addendum)

Validated by the papers; adopt without new machinery:

1. **Spec-first tasks**: milestone tasks get explicit requirements + expected
   interfaces before implementation, so the pytest gate discriminates success
   from valid-but-unexpected output. **[SWE-Bench Pro]**
2. **Verification failure is a named failure class** (premature termination,
   no/incorrect/weak verification). Done = gates observed green, never inferred.
   **[Terminal-Bench, Overthinking]**
3. **lessons.md 2.0 format** — keep dated append-only entries, but each entry
   adds: (a) measured outcome with numbers (e.g. "parse rate 12%→3%"),
   (b) a concrete trigger ("when parsing any PTR, before trusting amounts…"),
   (c) a "next applied" outcome line later. Rules derived from n=1 stay
   provisional until they pass gates on a *different* filing.
   **[EvoSkill, AlphaEvolve, DGM]**
4. **Freeze rule**: when one approach fails ≥3 times, freeze it and switch to an
   orthogonal strategy (e.g. regex → coordinate-based extraction); never retry a
   4th near-identical tweak. **[Bilevel Autoresearch, SWE-smith]**
5. **Bounded planning, early contact with reality**: short plans, executed
   immediately; inspect the actual artifact before reasoning about its format.
   **[Overthinking]**
6. **Check tool availability before building on it** (`which …` first — the
   pdftotext pattern). **[Terminal-Bench]**
7. **Prefer small Python tools with explicit success/failure output over shell
   one-liners** for mutations/extractions (silent no-op `sed` class of bug).
   **[Live-SWE-Agent]**
8. **Revert is the default on gate failure**; broken variants are discarded,
   not patched forward. **[DGM]**
9. **Do not build meta-learning/self-evolution machinery** — lessons.md +
   pytest/ruff/mypy gates are the right scale of self-improvement for this
   project. **[Live-SWE-Agent, DGM]**
10. **Focused subagents with narrow scopes** (reinforced by failure censuses:
    context overflow and endless file-reading are top agent failure modes).
    **[SWE-Bench Pro]**

## 5. Risk register (additions in bold)

| Risk | Mitigation |
|---|---|
| House PDF format drift | parser_version, golden fixtures (fail2pass/pass2pass), loud failures |
| Senate portal session/rate limits | polite client, raw caching, re-run-safe jobs |
| Ticker/issuer ambiguity | confidence-labeled mapping, never auto-map low confidence |
| Range midpoints mislead | store min/max with midpoint; certainty labels |
| Committee→sector subjectivity | versioned plain-data table, reviewable diffs |
| **Parser rules overfit to first filings seen** | **n=1 provisional-lesson rule; tune thresholds on held-out filings [MDL, EvoSkill]** |
| **Backtest lookahead bias** | **structural point-in-time enforcement + data-as-of labels [SWE-rebench]** |

## 6. Rough sizing (unchanged)

M1: 2–3 days · M2: 1–2 weeks (largest) · M3: ~1 week · M4: ~1 week · M5: 1–2 weeks.

## 7. Next actions

Execute M1 per §3-M1 on user approval, tracking in `tasks/todo.md`.

## 8. Paper relevance index (candid)

**Read in full, material integrated (12 agent/SWE papers):**
SWE-Bench Pro, Terminal-Bench 2.0, SWE-rebench, SWE-Gym, Live-SWE-Agent,
Darwin Gödel Machine, AlphaEvolve, EvoSkill, GLM-5, SWE-smith, Danger of
Overthinking, Bilevel Autoresearch.

**Classics — principle-level transfer only (5):** MDL tutorial (Grünwald),
Keeping Neural Networks Simple (Hinton & van Camp), Quantifying the Rise and
Fall of Complexity (Aaronson et al.), Scaling Laws (Kaplan et al.),
Mathematical Exploration at Scale (AlphaEvolve follow-up).

**Classics — no relevance to this project, do not revisit (17):** Attention Is
All You Need, ResNet ×2, AlexNet (file duplicated), Deep Speech 2, GPipe,
Relational Reasoning, Pointer Networks, Seq2Seq for Sets, NMT/Attention,
Neural Turing Machines, Relational RNNs, RNN Regularization, Neural Message
Passing, Variational Lossy Autoencoder, Dilated Convolutions, Machine Super
Intelligence.

**Filename caveat:** four files are mislabeled — "API based web agents",
"Demystifying LLM based software engineering agents", "LLM agents making agent
tools", "Generalist software engineering agents at scale" actually contain
SWE-Bench Pro, Terminal-Bench 2.0, SWE-rebench, and SWE-Gym respectively
(duplicates of correctly-named files). The papers those names describe do not
exist in the folder.
