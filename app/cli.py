"""Command-line entrypoint for Congress Alpha.

`init-db` creates tables. `ingest members|committees` runs the M1 pipeline:
snapshot raw datasets -> parse/validate -> idempotent upsert -> row-count
validation, with standard counters logged for each run. `parse`, `score`,
and `dashboard` remain stubs for later milestones.
"""

import argparse
import csv
import logging
from datetime import date
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import get_secret, get_settings
from app.core.logging import setup_logging
from app.db.models import Filing, Member, NetWorthEstimate, Position, ScoreComponent, Transaction
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
from app.intelligence.analysis import run_analysis, write_analysis_report
from app.intelligence.checkpoint import check_member
from app.intelligence.mappings import load_mappings
from app.intelligence.net_worth import estimate_net_worth
from app.intelligence.normalize import midpoint
from app.intelligence.notes import add_note, list_notes
from app.intelligence.positions import reconstruct_member
from app.intelligence.prices import fetch_daily_closes
from app.intelligence.scoring import run_scoring
from app.intelligence.services import export_rows
from app.intelligence.tiingo import fetch_daily_closes_tiingo
from app.intelligence.validation import run_validation, write_report
from app.jobs.refresh import core_ingest_stages, run_refresh
from app.parsing.house_fd import cross_check_fd, parse_fd_pdf
from app.parsing.house_ptr import cross_check, parse_ptr_pdf
from app.parsing.store import store_result
from app.parsing.store_fd import store_fd_result

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
    subparsers.add_parser(
        "reconstruct",
        help="rebuild estimated positions and net-worth estimates (M3)",
    )
    subparsers.add_parser("score", help="run the policy-edge scoring engine (M4)")
    subparsers.add_parser("export", help="write CSV exports to data/exports (M5a)")
    validate_parser = subparsers.add_parser(
        "validate", help="validate PTR purchase signals vs forward returns (M5b)"
    )
    validate_parser.add_argument(
        "--provider",
        choices=["tiingo", "yahoo"],
        default="tiingo",
        help="price data provider (default: tiingo)",
    )
    analyze_parser = subparsers.add_parser(
        "analyze", help="alpha-decay + signal-persistence analysis (M8)"
    )
    analyze_parser.add_argument(
        "--provider",
        choices=["tiingo", "yahoo"],
        default="tiingo",
        help="price data provider (default: tiingo)",
    )
    refresh_parser = subparsers.add_parser(
        "refresh", help="run the full daily refresh pipeline (M5b)"
    )
    refresh_parser.add_argument(
        "--download-limit",
        type=int,
        default=100,
        help="max filing documents to download per run (default: 100)",
    )

    note_parser = subparsers.add_parser("note", help="manage notes on members/filings")
    note_subparsers = note_parser.add_subparsers(dest="note_command", required=True)
    note_add = note_subparsers.add_parser("add", help="add a note")
    note_add.add_argument("--text", required=True, help="note body")
    note_add.add_argument("--member", dest="bioguide", default=None, help="member bioguide id")
    note_add.add_argument(
        "--filing", dest="doc_id", default=None, help="filing official doc id"
    )
    note_list = note_subparsers.add_parser("list", help="list notes")
    note_list.add_argument("--member", dest="bioguide", default=None, help="member bioguide id")
    note_list.add_argument(
        "--filing", dest="doc_id", default=None, help="filing official doc id"
    )
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
            if filing_type_raw == "A":
                fd_result = parse_fd_pdf(Path(filing.local_path or ""))
                warnings = cross_check_fd(fd_result)
                counters = store_fd_result(session, filing, fd_result)
                row_count = len(fd_result.holdings) + len(fd_result.liabilities)
                detail = (
                    f"{len(fd_result.holdings)} holdings + "
                    f"{len(fd_result.liabilities)} liabilities"
                )
            else:
                ptr_result = parse_ptr_pdf(Path(filing.local_path or ""))
                warnings = cross_check(
                    ptr_result, filing_date=filing.filing_date, expected_filer=filing.filer_name
                )
                counters = store_result(session, filing, ptr_result)
                row_count = len(ptr_result.transactions)
                detail = f"{row_count} tx"
            if row_count:
                filings_with_rows += 1
            totals.new += counters.new
            totals.unchanged += counters.unchanged
            logger.info(
                "parse doc %s: %s (%s)%s",
                filing.official_doc_id,
                detail,
                counters.summary(),
                f" warnings={warnings}" if warnings else "",
            )
        logger.info(
            "parse complete: %d/%d filings yielded rows; %s",
            filings_with_rows,
            len(filings),
            totals.summary(),
        )
    return 0


def _cmd_reconstruct() -> int:
    init_db()
    with session_scope() as session:
        # Backfill explicit midpoints on all parsed rows that have full ranges.
        backfilled = 0
        for tx in session.exec(select(Transaction)).all():
            if tx.amount_midpoint is None:
                tx.amount_midpoint = midpoint(tx.amount_min, tx.amount_max)
                if tx.amount_midpoint is not None:
                    backfilled += 1
        logger.info("midpoints backfilled on %d transactions", backfilled)

        # Deterministic rebuild: derived rows are always recomputable.
        for position_row in session.exec(select(Position)).all():
            session.delete(position_row)
        for estimate_row in session.exec(select(NetWorthEstimate)).all():
            session.delete(estimate_row)

        members = session.exec(select(Member)).all()
        total_positions = 0
        certainty_totals: dict[str, int] = {}
        net_worth_counts: dict[str, int] = {}
        members_with_fd = 0
        unobservable_sales = 0
        for member in members:
            assert member.id is not None
            report = check_member(session, member.id)
            stats = reconstruct_member(session, member.id, report)
            total_positions += stats.positions
            unobservable_sales += stats.unobservable_sales
            for label, count in stats.by_certainty.items():
                certainty_totals[label] = certainty_totals.get(label, 0) + count

            estimate = estimate_net_worth(session, report)
            if estimate is not None:
                session.add(estimate)
                members_with_fd += 1
                label = estimate.certainty.value
                net_worth_counts[label] = net_worth_counts.get(label, 0) + 1

        logger.info(
            "reconstruct complete: %d positions (%s) for %d members; "
            "net worth for %d members (%s); %d unobservable sales skipped",
            total_positions,
            certainty_totals,
            len(members),
            members_with_fd,
            net_worth_counts,
            unobservable_sales,
        )
    return 0


def _cmd_score() -> int:
    settings = get_settings()
    init_db()
    with session_scope() as session:
        mappings = load_mappings()
        if get_secret("TIINGO_API_KEY"):
            provider = lambda ticker: fetch_daily_closes_tiingo(ticker, settings.raw_dir)  # noqa: E731
        else:
            provider = None
            logger.warning("TIINGO_API_KEY not set: track_record component scores 0")
        run = run_scoring(session, mappings, price_provider=provider)
        rows = session.exec(
            select(ScoreComponent).where(
                ScoreComponent.score_run_id == run.id,
                ScoreComponent.component == "composite",
            )
        ).all()

        distribution: dict[str, int] = {}
        for row in rows:
            label = row.label.value if row.label else "unlabeled"
            distribution[label] = distribution.get(label, 0) + 1
        logger.info(
            "score run %d complete (config %s): label distribution %s",
            run.id,
            run.config_version,
            distribution,
        )

        top = sorted(rows, key=lambda r: r.value, reverse=True)[:5]
        for row in top:
            if row.value <= 0:
                continue
            member = session.get(Member, row.member_id)
            components = session.exec(
                select(ScoreComponent).where(
                    ScoreComponent.score_run_id == run.id,
                    ScoreComponent.member_id == row.member_id,
                    ScoreComponent.component != "composite",
                )
            ).all()
            best = max(components, key=lambda c: c.value)
            logger.info(
                "top overlap: %s %s composite=%.1f (%s) best=%s: %s",
                member.first_name if member else "?",
                member.last_name if member else "?",
                row.value,
                row.label,
                best.component,
                best.details,
            )
    return 0


def _resolve_member(session: Session, bioguide: str) -> Member | None:
    result: Member | None = session.exec(
        select(Member).where(Member.bioguide_id == bioguide)
    ).first()
    return result


def _cmd_note_add(text: str, bioguide: str | None, doc_id: str | None) -> int:
    init_db()
    with session_scope() as session:
        member_id = None
        filing_id = None
        if bioguide:
            member = _resolve_member(session, bioguide)
            if member is None:
                logger.error("no member with bioguide id %s", bioguide)
                return 2
            member_id = member.id
        if doc_id:
            filing = session.exec(
                select(Filing).where(Filing.official_doc_id == doc_id)
            ).first()
            if filing is None:
                logger.error("no filing with doc id %s", doc_id)
                return 2
            filing_id = filing.id
        if member_id is None and filing_id is None:
            logger.error("provide --member and/or --filing")
            return 2
        note = add_note(session, text, member_id=member_id, filing_id=filing_id)
        logger.info("note %d added", note.id)
    return 0


def _cmd_note_list(bioguide: str | None, doc_id: str | None) -> int:
    init_db()
    with session_scope() as session:
        member_id = None
        filing_id = None
        if bioguide:
            member = _resolve_member(session, bioguide)
            if member is None:
                logger.error("no member with bioguide id %s", bioguide)
                return 2
            member_id = member.id
        if doc_id:
            filing = session.exec(
                select(Filing).where(Filing.official_doc_id == doc_id)
            ).first()
            if filing is None:
                logger.error("no filing with doc id %s", doc_id)
                return 2
            filing_id = filing.id
        notes = list_notes(session, member_id=member_id, filing_id=filing_id)
        for note in notes:
            logger.info(
                "[%s] note %d (member=%s filing=%s): %s",
                note.created_at.date(),
                note.id,
                note.member_id,
                note.filing_id,
                note.body,
            )
        logger.info("%d note(s)", len(notes))
    return 0


def _cmd_export() -> int:
    settings = get_settings()
    init_db()
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    with session_scope() as session:
        for filename, (header, rows) in export_rows(session).items():
            path = settings.exports_dir / filename
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
            logger.info("export %s: %d rows", filename, len(rows))
    return 0


def _cmd_validate(provider_name: str) -> int:
    settings = get_settings()
    init_db()
    with session_scope() as session:
        if provider_name == "tiingo":
            provider = lambda ticker: fetch_daily_closes_tiingo(ticker, settings.raw_dir)  # noqa: E731
        else:
            provider = lambda ticker: fetch_daily_closes(ticker, settings.raw_dir)  # noqa: E731
        report = run_validation(session, provider)
        json_path, md_path = write_report(report, settings.exports_dir)
        for metrics in report.horizons:
            mean_excess = (
                f"{metrics.mean_excess_return:+.1%}"
                if metrics.mean_excess_return is not None
                else "n/a"
            )
            logger.info(
                "validation %dd: %d signals, mean excess %s, hit rate %s%s",
                metrics.horizon_days,
                metrics.signals,
                mean_excess,
                f"{metrics.hit_rate:.0%}" if metrics.hit_rate is not None else "n/a",
                " (THIN SAMPLE)" if metrics.thin_sample else "",
            )
        logger.info("validation report written: %s, %s", json_path, md_path)
    return 0


def _cmd_analyze(provider_name: str) -> int:
    settings = get_settings()
    init_db()
    with session_scope() as session:
        if provider_name == "tiingo":
            provider = lambda ticker: fetch_daily_closes_tiingo(ticker, settings.raw_dir)  # noqa: E731
        else:
            provider = lambda ticker: fetch_daily_closes(ticker, settings.raw_dir)  # noqa: E731
        report = run_analysis(session, provider)
        json_path, md_path = write_analysis_report(report, settings.exports_dir)
        overall = report.lag_decay["overall"]
        logger.info(
            "lag decay: %d signals, invisible mean %s (n=%d), copy 30d mean %s (n=%d)",
            overall["signals"],
            f"{overall['mean_invisible_excess']:+.1%}"
            if overall["mean_invisible_excess"] is not None
            else "n/a",
            overall["invisible_signals"],
            f"{overall['mean_copy_30d_excess']:+.1%}"
            if overall["mean_copy_30d_excess"] is not None
            else "n/a",
            overall["copy_30d_signals"],
        )
        for cohort, metrics in report.entry_vs_copy.items():
            for m in metrics:
                logger.info(
                    "%s %dd: %d signals, mean excess %s, hit rate %s",
                    cohort,
                    m.horizon_days,
                    m.signals,
                    f"{m.mean_excess_return:+.1%}" if m.mean_excess_return is not None else "n/a",
                    f"{m.hit_rate:.0%}" if m.hit_rate is not None else "n/a",
                )
        persistence = report.persistence
        logger.info(
            "persistence: %d qualifying members, spearman %s, top-decile retention %s",
            persistence["members"],
            f"{persistence['spearman']:.2f}" if persistence["spearman"] is not None else "n/a",
            f"{persistence['top_decile_retention']:.0%}"
            if persistence["top_decile_retention"] is not None
            else "n/a",
        )
        logger.info("analysis report written: %s, %s", json_path, md_path)
    return 0


def _cmd_refresh(download_limit: int) -> int:
    settings = get_settings()
    stages = core_ingest_stages(settings.raw_dir) + [
        ("filings", lambda: _cmd_ingest_filings(_default_filing_years(), refresh=False)),
        (
            "downloads",
            lambda: _cmd_ingest_downloads(
                limit=download_limit, refresh=False, filing_type_raw=None
            ),
        ),
        ("parse-ptr", lambda: _cmd_parse("P", None)),
        ("parse-fd", lambda: _cmd_parse("A", None)),
        ("reconstruct", _cmd_reconstruct),
        ("score", _cmd_score),
        ("export", _cmd_export),
    ]
    completed = run_refresh(stages)
    logger.info("refresh complete: %d/%d stages", len(completed), len(stages))
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
    if args.command == "reconstruct":
        return _cmd_reconstruct()
    if args.command == "score":
        return _cmd_score()
    if args.command == "export":
        return _cmd_export()
    if args.command == "validate":
        return _cmd_validate(args.provider)
    if args.command == "analyze":
        return _cmd_analyze(args.provider)
    if args.command == "refresh":
        return _cmd_refresh(args.download_limit)
    if args.command == "note":
        if args.note_command == "add":
            return _cmd_note_add(args.text, args.bioguide, args.doc_id)
        return _cmd_note_list(args.bioguide, args.doc_id)

    # Stub subcommands: intentional no-ops until later phases implement them.
    logger.info("'%s' is not implemented yet (Phase 0 scaffold).", args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
