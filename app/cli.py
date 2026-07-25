"""Command-line entrypoint for Congress Alpha.

`init-db` creates tables. `ingest members|committees` runs the M1 pipeline:
snapshot raw datasets -> parse/validate -> idempotent upsert -> row-count
validation, with standard counters logged for each run. `parse`, `score`,
and `dashboard` remain stubs for later milestones.
"""

import argparse
import logging
from datetime import date
from pathlib import Path

from sqlmodel import select

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.models import Filing
from app.db.session import init_db, session_scope
from app.ingestion.downloads import download_filings
from app.ingestion.house import fetch_index, index_zip_url, parse_index, upsert_filings
from app.ingestion.loaders import (
    table_counts,
    upsert_committees,
    upsert_members,
    upsert_memberships,
)
from app.ingestion.records import Counters, parse_committees, parse_members, parse_membership
from app.ingestion.sources import COMMITTEES, LEGISLATORS, MEMBERSHIP, snapshot_datasets
from app.parsing.house_ptr import cross_check, parse_ptr_pdf
from app.parsing.store import store_result

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

    filings_parser = ingest_subparsers.add_parser(
        "filings", help="ingest the House filing index into Filing rows"
    )
    filings_parser.add_argument(
        "--year",
        type=int,
        action="append",
        dest="years",
        help="filing year to ingest (repeatable; default: previous + current year)",
    )
    filings_parser.add_argument(
        "--refresh", action="store_true", help="re-download index snapshots"
    )

    downloads_parser = ingest_subparsers.add_parser(
        "downloads", help="download filing documents to data/raw"
    )
    downloads_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="maximum number of documents to download (default: 25)",
    )
    downloads_parser.add_argument(
        "--type",
        dest="filing_type_raw",
        default=None,
        help="restrict to one upstream filing-type code (e.g. P for PTRs)",
    )
    downloads_parser.add_argument(
        "--refresh", action="store_true", help="re-download documents"
    )

    parse_parser = subparsers.add_parser(
        "parse", help="parse downloaded filings into Transaction rows"
    )
    parse_parser.add_argument(
        "--type",
        dest="filing_type_raw",
        default="P",
        help="upstream filing-type code to parse (default: P = PTRs)",
    )
    parse_parser.add_argument(
        "--limit", type=int, default=None, help="max filings to parse (default: all)"
    )
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


def _default_filing_years() -> list[int]:
    """Previous and current calendar year — covers recent filings of current members."""
    today = date.today()
    return [today.year - 1, today.year]


def _cmd_ingest_filings(years: list[int], refresh: bool) -> int:
    settings = get_settings()
    init_db()
    with session_scope() as session:
        for year in years:
            xml_path = fetch_index(settings.raw_dir, year, refresh=refresh)
            records, skipped = parse_index(xml_path)
            counters = upsert_filings(session, records, index_zip_url(year))
            counters.skipped += skipped
            logger.info("ingest filings %d: %s", year, counters.summary())
    return 0


def _cmd_ingest_downloads(limit: int, refresh: bool, filing_type_raw: str | None) -> int:
    settings = get_settings()
    init_db()
    with session_scope() as session:
        counters = download_filings(
            session, settings.raw_dir, limit=limit, refresh=refresh,
            filing_type_raw=filing_type_raw,
        )
        logger.info("ingest downloads: %s", counters.summary())
    return 0


def _cmd_parse(filing_type_raw: str, limit: int | None) -> int:
    init_db()
    with session_scope() as session:
        query = (
            select(Filing)
            .where(
                Filing.filing_type_raw == filing_type_raw,
                Filing.local_path.is_not(None),  # type: ignore[union-attr]
            )
            .order_by(Filing.id)  # type: ignore[arg-type]
        )
        if limit is not None:
            query = query.limit(limit)
        filings = session.exec(query).all()

        totals = Counters()
        filings_with_rows = 0
        for filing in filings:
            result = parse_ptr_pdf(Path(filing.local_path or ""))
            warnings = cross_check(
                result, filing_date=filing.filing_date, expected_filer=filing.filer_name
            )
            counters = store_result(session, filing, result)
            if result.transactions:
                filings_with_rows += 1
            totals.new += counters.new
            totals.unchanged += counters.unchanged
            logger.info(
                "parse doc %s: %d tx (%s)%s",
                filing.official_doc_id,
                len(result.transactions),
                counters.summary(),
                f" warnings={warnings}" if warnings else "",
            )
        logger.info(
            "parse complete: %d/%d filings yielded transactions; %s",
            filings_with_rows,
            len(filings),
            totals.summary(),
        )
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
        if args.ingest_target == "committees":
            return _cmd_ingest_committees(refresh=args.refresh)
        if args.ingest_target == "filings":
            return _cmd_ingest_filings(args.years or _default_filing_years(), args.refresh)
        return _cmd_ingest_downloads(
            limit=args.limit, refresh=args.refresh, filing_type_raw=args.filing_type_raw
        )
    if args.command == "parse":
        return _cmd_parse(args.filing_type_raw, args.limit)

    # Stub subcommands: intentional no-ops until later phases implement them.
    logger.info("'%s' is not implemented yet (Phase 0 scaffold).", args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
