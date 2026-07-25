# Task: Phase 0 project scaffold

## Context
- Build the initial local-first project scaffold for the Congress Alpha MVP.
- The scaffold should support future ingestion, scoring, and dashboard work without implementing those features yet.
- Use the workflow in `CLAUDE.md` before coding non-trivial changes.

## Plan
- [x] Confirm the Phase 0 scope and constraints.
- [x] Create the base repository structure (`app/`, `tests/`, `tasks/`, `data/`).
- [x] Create `pyproject.toml` with Python/tooling dependencies.
- [x] Create initial package modules and `__init__.py` files.
- [x] Create database base/session/models skeleton.
- [x] Create starter config/logging/enums modules.
- [x] Create a minimal CLI entrypoint.
- [x] Create placeholder tests that validate imports / basic structure.
- [x] Run lint/tests/type checks where configured.
- [x] Update this file with verification details and review notes.

## Notes
- Executed as a goal-mode run per the approved end-to-end plan (§3-M0); scope held to M0 only.
- Environment: `uv` 0.11.32 installed via Homebrew (authorized); CPython 3.12.13 provisioned by uv and pinned in `.python-version`.
- `uv sync` created `.venv` and installed 58 packages (sqlmodel 0.0.39, streamlit 1.60.0, pandas 3.0.5, httpx 0.28.1; dev: pytest 9.1.1, ruff 0.16.0, mypy 2.3.0).
- `todo.md`/`lessons.md` moved from project root into `tasks/` per the required layout.
- No real ingestion, parsing, or scoring implemented — stubs only, per Phase 0 scope.
- SQLModel chosen (over raw SQLAlchemy) for typed models; CLI uses stdlib `argparse` to keep deps minimal.
- Business logic remains outside any future dashboard code; layers are separate packages.

## Verification
- [x] Tests run — `uv run pytest`: 6 passed (import smoke, enum sanity, in-memory SQLite create_all + Member roundtrip).
- [x] Lint run — `uv run ruff check .`: all checks passed.
- [x] Type checks run — `uv run mypy app`: no issues found in 14 source files.
- [x] Manual verification completed — `uv run python -m app.cli init-db` created `data/congress_alpha.db` (110 KB, all tables); stub `ingest` logs its not-implemented message; throwaway DB removed after check.

## Review
- Summary of what changed: created the full Phase 0 scaffold — `pyproject.toml` (Python 3.12 pin, ruff/mypy/pytest config), `README.md`, `.python-version`, `app/` package tree (core/db/ingestion/parsing/intelligence/dashboard/jobs), `app/core` (config, logging, enums), `app/db` (engine/session factory + 11 skeletal tables with traceability fields), `app/cli.py` (init-db real, ingest/parse/score/dashboard stubs), 3 placeholder test files, `data/` and `tasks/` directories.
- Risks / follow-ups:
  - `data/congress_alpha.db` is recreated on demand by `init-db`; add `.gitignore` (for `.venv/`, `data/*.db`, `__pycache__`) when a git repo is initialized — no git mutations were made in this phase.
  - mypy runs with pragmatic settings (`disallow_untyped_defs` etc.), not full `strict`; revisit if stricter typing is wanted in Phase 1+.
  - Streamlit/pandas are installed but unused until Phase 5/3 — expected, keeps later phases unblocked.
  - Next milestone per plan: M1 (member + committee ingestion), only when the user asks.
