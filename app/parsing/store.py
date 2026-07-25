"""Persist PTR parse results as Transaction/Asset rows.

Idempotency rule: a filing's rows are replaced only when PARSER_VERSION
changes; same-version re-runs skip. Asset rows are deduplicated by exact
verbatim (name, ticker) — no normalization here; that is M3's job.
"""

import logging

from sqlmodel import Session, select

from app.core.enums import AssetType
from app.db.models import Asset, Filing, Transaction
from app.ingestion.records import Counters
from app.parsing.house_ptr import PARSER_VERSION, PtrParseResult

logger = logging.getLogger(__name__)

_ASSET_TYPE_MAP = {
    "ST": AssetType.STOCK,
    "OP": AssetType.STOCK_OPTION,
}


def _get_or_create_asset(
    session: Session, name: str, ticker: str | None, asset_type_code: str | None
) -> int:
    existing = session.exec(
        select(Asset).where(Asset.name == name, Asset.ticker == ticker)
    ).first()
    if existing is not None and existing.id is not None:
        return existing.id
    asset = Asset(
        name=name,
        ticker=ticker,
        asset_type=_ASSET_TYPE_MAP.get(asset_type_code or "", AssetType.OTHER),
    )
    session.add(asset)
    session.flush()
    assert asset.id is not None
    return asset.id


def store_result(session: Session, filing: Filing, result: PtrParseResult) -> Counters:
    """Store a parse result for a filing; skip when already parsed at this version."""
    counters = Counters(total=len(result.transactions))
    existing = session.exec(
        select(Transaction).where(Transaction.filing_id == filing.id)
    ).all()
    if existing and filing.parser_version == PARSER_VERSION:
        counters.unchanged = len(existing)
        return counters

    for old in existing:  # parser version bumped: replace old-version rows
        session.delete(old)
    for tx in result.transactions:
        asset_id = _get_or_create_asset(session, tx.asset_name, tx.ticker, tx.asset_type_code)
        session.add(
            Transaction(
                filing_id=filing.id,
                asset_id=asset_id,
                owner=tx.owner,
                transaction_type=tx.transaction_type,
                transaction_date=tx.transaction_date,
                notification_date=tx.notification_date,
                amount_min=tx.amount_min,
                amount_max=tx.amount_max,
                raw_text=tx.raw_text,
                parse_confidence=tx.parse_confidence,
                parser_version=PARSER_VERSION,
            )
        )
        counters.new += 1
    filing.parser_version = PARSER_VERSION
    return counters
