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

## Dashboard

```bash
uv run streamlit run app/dashboard/main.py   # http://localhost:8501
```

## Daily refresh

Run the full pipeline (ingest → downloads → parse → reconstruct → score → export):

```bash
uv run python -m app.cli refresh
```

Schedule it daily with cron (runs at 06:17 local time; adjust as you like):

```cron
17 6 * * * cd /Users/tomcruise/Projects/Congress-Alpha && /opt/homebrew/bin/uv run python -m app.cli refresh >> data/refresh.log 2>&1
```

## Historical validation

Validate PTR purchase signals against forward returns (filing-date t0, excess
vs SPY; prices from Yahoo Finance's public chart endpoint, cached locally):

```bash
uv run python -m app.cli validate   # writes data/exports/validation_report.{json,md}
```
