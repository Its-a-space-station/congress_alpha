"""Command-line entrypoint for Congress Alpha.

`init-db` creates tables. `ingest members|committees` runs the M1 pipeline:
snapshot raw datasets -> parse/validate -> idempotent upsert -> row-count
validation, with standard counters logged for each run. `parse`, `score`,
and `dashboard` remain stubs for later milestones.
"""

import argparse
import logging

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import init_db, session_scope
from app.ingestion.loaders import (
    table_counts,
    upsert_committees,
    upsert_members,
    upsert_memberships,
)
from app.ingestion.records import parse_committees, parse_members, parse_membership
from app.ingestion.sources import COMMITTEES, LEGISLATORS, MEMBERSHIP, snapshot_datasets

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="congress-alpha",
        description="Local-first congressional disclosure research engine",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create all database tables")

    ingest_parser = subparsers.add_parser("ingest", help="ingest official source data")
    ingest_subparsers = ingest_parser.add_subparsers(dest="ingest_target", required=True)
    for target in ("members", "committees"):
        target_parser = ingest_subparsers.add_parser(target, help=f"ingest {target} data")
        target_parser.add_argument(
            "--refresh",
            action="store_true",
            help="re-download raw snapshots instead of using the cache",
        )

    subparsers.add_parser("parse", help="(stub) parse downloaded filings")
    subparsers.add_parser("score", help="(stub) run the scoring engine")
    subparsers.add_parser("dashboard", help="(stub) launch the Streamlit dashboard")
    return parser


def _cmd_ingest_members(refresh: bool) -> int:
    settings = get_settings()
    paths = snapshot_datasets(settings.raw_dir, refresh=refresh)
    init_db()
    with session_scope() as session:
        records, skipped = parse_members(paths[LEGISLATORS.filename])
        counters = upsert_members(session, records, LEGISLATORS.url)
        counters.skipped += skipped
        logger.info("ingest members: %s", counters.summary())
        logger.info("row counts after load: %s", table_counts(session))
    return 0


def _cmd_ingest_committees(refresh: bool) -> int:
    settings = get_settings()
    paths = snapshot_datasets(settings.raw_dir, refresh=refresh)
    init_db()
    with session_scope() as session:
        committees, skipped_committees = parse_committees(paths[COMMITTEES.filename])
        committee_counters = upsert_committees(session, committees, COMMITTEES.url)
        committee_counters.skipped += skipped_committees
        logger.info("ingest committees: %s", committee_counters.summary())

        memberships, skipped_memberships = parse_membership(
            paths[MEMBERSHIP.filename], {c.code for c in committees}
        )
        membership_counters = upsert_memberships(session, memberships, MEMBERSHIP.url)
        membership_counters.skipped += skipped_memberships
        logger.info("ingest committee memberships: %s", membership_counters.summary())
        logger.info("row counts after load: %s", table_counts(session))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if args.command == "init-db":
        init_db()
        logger.info("Database initialized at %s", settings.db_url)
        return 0
    if args.command == "ingest":
        if args.ingest_target == "members":
            return _cmd_ingest_members(refresh=args.refresh)
        return _cmd_ingest_committees(refresh=args.refresh)

    # Stub subcommands: intentional no-ops until later phases implement them.
    logger.info("'%s' is not implemented yet (Phase 0 scaffold).", args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
