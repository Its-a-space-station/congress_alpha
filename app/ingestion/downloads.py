"""Downloader stage (M2a): fetch filing documents to data/raw with checksums.

Idempotent: filings that already have `local_path` are skipped. Rate-paced to
be polite to the host. The command is generic over the whole filings table;
callers bound the work with `limit`.
"""

import logging
import time
from pathlib import Path

from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.core.enums import Chamber
from app.db.models import Filing
from app.ingestion.house import doc_pdf_url
from app.ingestion.http import fetch, sha256_bytes
from app.ingestion.records import Counters

logger = logging.getLogger(__name__)

DOWNLOAD_SUBDIR = "filings"
PACE_SECONDS = 0.5


def download_filings(
    session: Session,
    raw_dir: Path,
    *,
    chamber: Chamber = Chamber.HOUSE,
    limit: int = 25,
    refresh: bool = False,
    filing_type_raw: str | None = None,
) -> Counters:
    """Download up to `limit` not-yet-downloaded filings; record path + sha256.

    `filing_type_raw` optionally restricts to one upstream type code (e.g. "P"
    for PTRs). Documents from index years <= 2014 are excluded: the Clerk's
    document service does not carry them at the current public_disc paths
    (verified 2026-07-25 across multiple DocIDs) — they are counted as
    skipped, never network-attempted.
    """
    query = select(Filing).where(
        Filing.chamber == chamber,
        Filing.local_path.is_(None),  # type: ignore[union-attr]
        or_(
            Filing.index_year > 2014,  # type: ignore[operator,arg-type]
            Filing.index_year.is_(None),  # type: ignore[union-attr]
        ),
    )
    if filing_type_raw is not None:
        query = query.where(Filing.filing_type_raw == filing_type_raw)
    pending = session.exec(
        query.order_by(Filing.id).limit(limit)  # type: ignore[arg-type]
    ).all()
    early_excluded = session.exec(
        select(func.count()).select_from(Filing).where(
            Filing.chamber == chamber,
            Filing.local_path.is_(None),  # type: ignore[union-attr]
            Filing.index_year <= 2014,  # type: ignore[operator]
        )
    ).one()
    if early_excluded:
        logger.info(
            "skipping %d pre-2015 documents (unavailable at current public_disc paths)",
            early_excluded,
        )
    counters = Counters(total=len(pending))

    for filing in pending:
        if not (filing.official_doc_id and filing.index_year):
            counters.skipped += 1
            logger.warning("skipping filing id=%s: no doc id or index year", filing.id)
            continue
        url = doc_pdf_url(filing.index_year, filing.official_doc_id, filing.filing_type_raw)
        path = raw_dir / DOWNLOAD_SUBDIR / chamber.value / f"{filing.official_doc_id}.pdf"
        try:
            payload = fetch(url, path, refresh=refresh)
        except RuntimeError as exc:
            # One unreachable document must not abort the batch.
            counters.skipped += 1
            logger.warning("download failed for doc %s: %s", filing.official_doc_id, exc)
            continue
        filing.local_path = str(path)
        filing.checksum = sha256_bytes(payload)
        counters.new += 1
        time.sleep(PACE_SECONDS)
    return counters
