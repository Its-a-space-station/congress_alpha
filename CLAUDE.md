# CLAUDE.md — Congress Alpha

This repository is for a local-first research engine that analyzes public congressional financial disclosures, estimates current household exposure for current members of Congress and disclosed spouses/partners, and ranks names with a conservative policy-overlap score for investment idea generation.

The assistant working in this repo must follow the workflow rules below for every non-trivial task.

---

## Project Mission

Build a conservative, auditable monitoring system that:
- ingests official House and Senate disclosure data,
- reconstructs likely open positions using annual disclosures plus periodic transaction reports,
- estimates household net worth from annual disclosures with certainty labels,
- scores committee/company and committee/sector overlap,
- surfaces ranked watchlists and sector clusters,
- supports historical validation,
- stays structured for a later migration from local app to web app.

This project is **not** for making accusations of insider trading. Use labels such as:
- policy-edge score
- conflict-risk signal
- policy-exposure overlap
- plausible informational advantage

---

## Boris-Style Workflow Rules

These rules are adapted from the user's uploaded Boris Cherny workflow document. Review them before starting meaningful work. fileciteturn4file0

### 1) Plan Node Default
- Enter plan mode for any non-trivial task: anything with 3+ steps, architectural choices, schema changes, parser changes, scoring changes, dashboard structure changes, or debugging that is not obviously one-line.
- If something starts going sideways, stop and re-plan instead of pushing through with a weak plan.
- Use plan mode for verification work too, not just implementation.
- Write detailed specs upfront to reduce ambiguity.

### 2) Subagent Strategy
- Use focused subagents or isolated work streams liberally when they reduce confusion and keep the main context clean.
- Offload research, exploration, and parallel analysis where helpful.
- One clearly defined task per subagent/work stream.
- Merge outputs only after validating them.

### 3) Self-Improvement Loop
- After any user correction, update `tasks/lessons.md` with the pattern.
- Convert corrections into concrete preventive rules.
- Review relevant lessons before starting a related task.
- The goal is to reduce repeated mistakes over time.

### 4) Verification Before Done
- Never mark a task complete without proving it works.
- Compare behavior before vs after when relevant.
- Run tests, inspect logs, and demonstrate correctness.
- Ask: would a strong staff engineer approve this as complete?

### 5) Demand Elegance (Balanced)
- For non-trivial changes, pause and ask whether there is a cleaner solution.
- If a fix feels hacky, step back and implement the elegant version if practical.
- Do not over-engineer trivial tasks.
- Challenge your own design before presenting it.

### 6) Autonomous Bug Fixing
- When given a bug report, investigate and fix it without asking for unnecessary hand-holding.
- Point to the evidence: logs, stack traces, failing tests, bad outputs.
- Find the root cause; avoid temporary patches unless explicitly required.
- Resolve failing tests/CI issues directly.

The source workflow emphasizes plan-first task management, lessons capture, verification before done, elegance checks, and autonomous bug-fixing. fileciteturn4file0

---

## Mandatory Task Management

For any non-trivial task:

1. Write a plan to `tasks/todo.md` using checkable items.
2. Show or summarize the plan before implementation.
3. Mark items complete as you go.
4. Add a short high-level summary of what changed at each major step.
5. Add a `Review` section to `tasks/todo.md` before calling the task done.
6. After user corrections, update `tasks/lessons.md`.

### Required `tasks/todo.md` structure
Use this shape unless there is a strong reason not to:

```md
# Task: <short title>

## Context
- ...

## Plan
- [ ] Step 1
- [ ] Step 2
- [ ] Step 3

## Notes
- ...

## Verification
- [ ] Tests run
- [ ] Lint run
- [ ] Type checks run
- [ ] Manual verification completed

## Review
- Summary of what changed
- Risks / follow-ups
```

### Required `tasks/lessons.md` structure
Use append-only dated entries:

```md
## YYYY-MM-DD — Lesson title
- What went wrong
- Preventive rule
- How to check for it next time
```

---

## Engineering Principles for This Repo

The uploaded workflow highlights simplicity, root-cause thinking, and minimal-impact changes. Apply that standard here. fileciteturn4file0

- Keep changes as simple as possible.
- Touch the minimum necessary surface area.
- Prefer auditable logic over cleverness.
- Preserve raw source text wherever parsing occurs.
- Every derived metric must state its method and confidence.
- Avoid hidden business logic in the UI layer.
- Design for future migration to a web app.

### Architecture principles
- Business logic belongs in service modules, not dashboard code.
- Ingestion, parsing, intelligence, and presentation must stay separate.
- Use typed models and explicit confidence labels.
- Favor deterministic scoring over opaque heuristics.
- Store source URLs and parser versions for traceability.

---

## Product Scope Guardrails

### In scope for MVP
- Current members of Congress
- Spouses/partners where disclosed
- Individual stocks and options
- Annual disclosures + periodic transaction reports
- Midpoint value estimation for ranges
- Estimated open positions
- Net worth estimates from annual disclosures with low/medium/high certainty
- Committee/company overlap
- Committee/sector overlap
- Repeat-pattern detection
- Ranked watchlist/dashboard
- Historical validation hooks

### Out of scope for MVP
- Paid/commercial distribution assumptions
- Accusatory language or claims of wrongdoing
- 13F clustering in phase 0/1
- USASpending correlation in phase 0/1
- Hiring-surge monitoring in MVP
- Full web app deployment in MVP
- Overly aggressive options strategy reconstruction

---

## Expected Repository Layout

```text
congress-alpha/
├── app/
│   ├── core/
│   ├── db/
│   ├── ingestion/
│   ├── parsing/
│   ├── intelligence/
│   ├── dashboard/
│   ├── jobs/
│   └── cli.py
├── tests/
├── data/
│   ├── raw/
│   ├── processed/
│   └── exports/
├── tasks/
│   ├── todo.md
│   └── lessons.md
├── pyproject.toml
├── .env
└── README.md
```

---

## Default Tech Choices

Use these unless the user explicitly changes direction:
- Python 3.12
- `uv` for environment/package management
- SQLite for local-first MVP
- SQLAlchemy or SQLModel for persistence
- Streamlit for the initial dashboard
- pandas/polars where appropriate
- pytest for tests
- ruff + mypy for quality checks
- APScheduler or cron for the daily refresh job

---

## Coding Standards

- Use clear names over short names.
- Keep functions small and composable.
- Add docstrings where intent is not obvious.
- Fail loudly on parser ambiguity; preserve raw text.
- Every score should be decomposable into components.
- Prefer explicit enums for chamber, filing type, owner type, asset type, transaction type, certainty labels, and score labels.
- Add tests for parsers, estimators, and scoring logic.

### For parsers
- Preserve raw source text.
- Record parse confidence.
- Handle missing/ambiguous issuer names gracefully.
- Do not silently map low-confidence issuer strings to tickers.

### For estimators
- State assumptions in code comments and docs.
- Return both value and confidence/certainty.
- Make midpoint logic explicit for disclosed ranges.

### For the dashboard
- UI must consume service outputs, not reimplement logic.
- Provide manual notes/comments fields where applicable.
- Favor filters and explainability over visual complexity.

---

## Verification Requirements

Before calling any non-trivial task done:
- run relevant tests,
- run lint,
- run type checks if applicable,
- perform a quick manual validation,
- update `tasks/todo.md` review section,
- note any unresolved risks.

Minimum expected commands once the scaffold exists:

```bash
uv run pytest
uv run ruff check .
uv run mypy app
```

If one of these is not yet configured, say so explicitly and verify what is available.

---

## How to Work in Phases

### Phase 0
Project scaffold only:
- package manager and project metadata
- app/tests/tasks/data directories
- DB base/session/models skeleton
- toolchain setup
- no real ingestion yet

### Phase 1
Official member + committee ingestion

### Phase 2
Disclosure ingestion and parsing

### Phase 3
Normalization and position reconstruction

### Phase 4
Scoring + comments engine

### Phase 5
Dashboard and historical validation

### Phase 6+
13F clustering, USAspending, web migration path

Do not skip ahead unless the user asks.

---

## Done Criteria

A task is only done when:
- the requested scope is implemented,
- the plan checklist is updated,
- verification is documented,
- the change is understandable and minimally invasive,
- the result is strong enough that you would be comfortable maintaining it.

When in doubt, choose the simpler, more auditable design.

---

## Checkpoint Convention

When the user says **"checkpoint"** (optionally with a message, e.g. `checkpoint: add parser`):

1. Stage all changes (`git add -A` — `.gitignore` keeps generated/local files out).
2. Commit with a concise message summarizing the diff; use the user's message if provided.
3. Push to `origin main` (https://github.com/Its-a-space-station/congress_alpha).

This is a standing authorization: the word "checkpoint" is the explicit request — do not
re-ask for confirmation. All other git mutations (rebase, reset, force-push, branching,
history edits) still require explicit confirmation each time.
