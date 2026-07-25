"""Daily refresh pipeline (M5b). One function, all stages, loud failure.

Designed for cron/launchd (see README.md) — no long-running daemon. Each
stage logs its counters; a stage failure aborts the run with the stage name
so the caller sees exactly what broke. Re-runnable: every stage is
idempotent (cache hits, upserts, version-gated parses, deterministic rebuild).
"""

import logging
from collections.abc import Callable
from pathlib import Path

from app.db.session import init_db, session_scope
from app.ingestion.loaders import upsert_committees, upsert_members, upsert_memberships
from app.ingestion.records import parse_committees, parse_members, parse_membership
from app.ingestion.sources import COMMITTEES, LEGISLATORS, MEMBERSHIP, snapshot_datasets

logger = logging.getLogger(__name__)


def run_refresh(
    stages: list[tuple[str, Callable[[], object]]],
) -> list[str]:
    """Run pipeline stages in order; stop loudly at the first failure.

    `stages` is a list of (name, callable). Returns the completed stage
    names. Raises RuntimeError naming the failed stage.
    """
    completed: list[str] = []
    for name, stage in stages:
        logger.info("refresh stage start: %s", name)
        try:
            stage()
        except Exception as exc:
            raise RuntimeError(f"refresh failed at stage '{name}': {exc}") from exc
        completed.append(name)
        logger.info("refresh stage done: %s", name)
    return completed


def core_ingest_stages(raw_dir: Path) -> list[tuple[str, Callable[[], object]]]:
    """The member/committee ingest stages, bound to a raw-data directory."""

    def members() -> None:
        paths = snapshot_datasets(raw_dir)
        init_db()
        with session_scope() as session:
            records, _ = parse_members(paths[LEGISLATORS.filename])
            counters = upsert_members(session, records, LEGISLATORS.url)
            logger.info("members: %s", counters.summary())

    def committees() -> None:
        paths = snapshot_datasets(raw_dir)
        init_db()
        with session_scope() as session:
            committee_records, _ = parse_committees(paths[COMMITTEES.filename])
            counters = upsert_committees(session, committee_records, COMMITTEES.url)
            membership_records, _ = parse_membership(
                paths[MEMBERSHIP.filename], {c.code for c in committee_records}
            )
            membership_counters = upsert_memberships(session, membership_records, MEMBERSHIP.url)
            logger.info(
                "committees: %s; memberships: %s",
                counters.summary(),
                membership_counters.summary(),
            )

    return [("members", members), ("committees", committees)]
