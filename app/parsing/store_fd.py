"""Persist annual-FD parse results as Holding/Liability rows.

Same idempotency contract as the PTR store: a filing's rows are replaced only
when PARSER_VERSION changes; same-version re-runs skip. Holding assets are
deduplicated by exact verbatim (name, ticker) — no normalization (M3's job).
"""

import logging

from sqlmodel import Session, select

from app.db.models import Filing, Holding, Liability
from app.ingestion.records import Counters
from app.parsing.house_fd import PARSER_VERSION, FdParseResult
from app.parsing.store import _get_or_create_asset

logger = logging.getLogger(__name__)


def store_fd_result(session: Session, filing: Filing, result: FdParseResult) -> Counters:
    """Store holdings + liabilities for a filing; skip when already at this version."""
    counters = Counters(total=len(result.holdings) + len(result.liabilities))
    existing_holdings = session.exec(
        select(Holding).where(Holding.filing_id == filing.id)
    ).all()
    existing_liabilities = session.exec(
        select(Liability).where(Liability.filing_id == filing.id)
    ).all()
    if (existing_holdings or existing_liabilities) and filing.parser_version == PARSER_VERSION:
        counters.unchanged = len(existing_holdings) + len(existing_liabilities)
        return counters

    for old in [*existing_holdings, *existing_liabilities]:  # version bump: replace
        session.delete(old)

    for holding in result.holdings:
        asset_id = None
        if holding.asset_name:
            asset_id = _get_or_create_asset(
                session, holding.asset_name, holding.ticker, holding.asset_type_code
            )
        session.add(
            Holding(
                filing_id=filing.id,
                asset_id=asset_id,
                account=holding.account,
                owner=holding.owner,
                value_min=holding.value_min,
                value_max=holding.value_max,
                income_details=holding.income_details,
                description=holding.description,
                location=holding.location,
                raw_text=holding.raw_text,
                parse_confidence=holding.parse_confidence,
                parser_version=PARSER_VERSION,
            )
        )
        counters.new += 1

    for liability in result.liabilities:
        session.add(
            Liability(
                filing_id=filing.id,
                owner=liability.owner,
                creditor_name=liability.creditor_name,
                liability_type=liability.liability_type,
                date_incurred=liability.date_incurred,
                value_min=liability.value_min,
                value_max=liability.value_max,
                raw_text=liability.raw_text,
                parse_confidence=liability.parse_confidence,
                parser_version=PARSER_VERSION,
            )
        )
        counters.new += 1

    filing.parser_version = PARSER_VERSION
    return counters
