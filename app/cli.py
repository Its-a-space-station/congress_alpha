"""Command-line entrypoint for Congress Alpha.

Phase 0: only `init-db` does real work. The other subcommands are explicit
stubs so the CLI shape is settled before later phases fill them in.
"""

import argparse
import logging

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import init_db

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="congress-alpha",
        description="Local-first congressional disclosure research engine",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create all database tables")
    subparsers.add_parser("ingest", help="(stub) ingest official source data")
    subparsers.add_parser("parse", help="(stub) parse downloaded filings")
    subparsers.add_parser("score", help="(stub) run the scoring engine")
    subparsers.add_parser("dashboard", help="(stub) launch the Streamlit dashboard")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if args.command == "init-db":
        init_db()
        logger.info("Database initialized at %s", settings.db_url)
        return 0

    # Stub subcommands: intentional no-ops until later phases implement them.
    logger.info("'%s' is not implemented yet (Phase 0 scaffold).", args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
