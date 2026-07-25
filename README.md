# Congress Alpha

Local-first research engine that analyzes public congressional financial disclosures,
estimates household exposure for current members of Congress and disclosed
spouses/partners, and ranks names with a conservative policy-overlap score for
investment idea generation.

This project is not for making accusations of insider trading. See `CLAUDE.md` for
the project mission, workflow rules, and scope guardrails.

## Status

Phase 0 (project scaffold) — no real ingestion, parsing, or scoring yet.

## Layout

```text
app/            # application package (core, db, ingestion, parsing, intelligence, dashboard, jobs)
tests/          # pytest suite
data/           # raw / processed / exports (local data, not committed)
tasks/          # todo.md and lessons.md task tracking
```

## Quickstart

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy app
uv run python -m app.cli --help
```
