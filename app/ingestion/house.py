"""House Clerk financial-disclosure index ingestion (M2a).

Verified 2026-07-24 (see tasks/todo.md M2a notes):
- Annual index ZIP: public_disc/financial-pdfs/{year}FD.zip containing
  {year}FD.xml with one <Member> record per filing (not per person).
- Documents: PTRs under public_disc/ptr-pdfs/{year}/{DocID}.pdf, all other
  types under public_disc/financial-pdfs/{year}/{DocID}.pdf
- Index is republished daily; FilingType is a single-letter code. Codes mapped
  below are confirmed by the Clerk's own search-result labels; all other codes
  map to OTHER with the raw code preserved (never guessed).
"""

import logging
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from sqlmodel import Session, select

from app.core.enums import Chamber, FilingType
from app.db.models import Filing, Member
from app.ingestion.http import fetch
from app.ingestion.records import Counters

logger = logging.getLogger(__name__)

BASE_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs"
PTR_BASE_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs"
INDEX_SUBDIR = "house_index"

# Confirmed against the Clerk's search-result labels (P -> "PTR",
# X -> "Extension"; A/C/T per the Clerk's published filing-type list).
# All other observed codes (B, D, E, G, H, O, W) stay OTHER with the raw
# code preserved until the Clerk's full legend is confirmed.
_FILING_TYPE_MAP = {
    "A": FilingType.ANNUAL,
    "C": FilingType.CANDIDATE,
    "P": FilingType.PERIODIC_TRANSACTION,
    "T": FilingType.TERMINATION,
    "X": FilingType.EXTENSION,
}


def index_zip_url(year: int) -> str:
    """URL of the annual filing-index ZIP for a filing year."""
    return f"{BASE_URL}/{year}FD.zip"


def doc_pdf_url(year: int, doc_id: str, filing_type_raw: str | None = None) -> str:
    """URL of a filing's PDF document.

    PTRs ("P") live under ptr-pdfs/; all other filing types live under
    financial-pdfs/ (verified 2026-07-24 with doc 20032062 vs 30023997).
    """
    if filing_type_raw == "P":
        return f"{PTR_BASE_URL}/{year}/{doc_id}.pdf"
    return f"{BASE_URL}/{year}/{doc_id}.pdf"


@dataclass(frozen=True)
class HouseFilingRecord:
    """One filing row from the House index XML."""

    last_name: str
    first_name: str
    prefix: str | None
    suffix: str | None
    filing_type_raw: str
    state_district: str
    index_year: int
    filing_date: date | None
    doc_id: str

    @property
    def filer_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def fetch_index(raw_dir: Path, year: int, *, refresh: bool = False) -> Path:
    """Snapshot the index ZIP for `year` and return the extracted XML path."""
    snapshot_dir = raw_dir / INDEX_SUBDIR
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    zip_path = snapshot_dir / f"{year}FD.zip"
    payload = fetch(index_zip_url(year), zip_path, refresh=refresh)

    xml_path = snapshot_dir / f"{year}FD.xml"
    if refresh or not xml_path.exists() or xml_path.stat().st_mtime < zip_path.stat().st_mtime:
        with zipfile.ZipFile(zip_path) as archive:
            xml_path.write_bytes(archive.read(f"{year}FD.xml"))
        logger.info("extracted %s (%d bytes)", xml_path, len(payload))
    return xml_path


def _text(element: ElementTree.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _parse_filing_date(value: str | None) -> date | None:
    if not value:
        return None
    parts = value.split("/")  # index format: M/D/YYYY
    if len(parts) != 3:
        return None
    try:
        month, day, year = (int(p) for p in parts)
        return date(year, month, day)
    except ValueError:
        return None


def parse_index(xml_path: Path) -> tuple[list[HouseFilingRecord], int]:
    """Parse the index XML into records; malformed entries are skipped + counted."""
    tree = ElementTree.parse(xml_path)  # noqa: S314 - trusted official source snapshot
    root = tree.getroot()

    records: list[HouseFilingRecord] = []
    skipped = 0
    for member in root.iter("Member"):
        doc_id = _text(member, "DocID")
        last = _text(member, "Last")
        filing_type_raw = _text(member, "FilingType")
        state_district = _text(member, "StateDst")
        year_text = _text(member, "Year")
        if not (doc_id and last and filing_type_raw and state_district and year_text):
            skipped += 1
            logger.warning("skipping index entry (missing required field): doc_id=%r", doc_id)
            continue
        records.append(
            HouseFilingRecord(
                last_name=last,
                first_name=_text(member, "First") or "",
                prefix=_text(member, "Prefix"),
                suffix=_text(member, "Suffix"),
                filing_type_raw=filing_type_raw,
                state_district=state_district,
                index_year=int(year_text),
                filing_date=_parse_filing_date(_text(member, "FilingDate")),
                doc_id=doc_id,
            )
        )
    return records, skipped


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z ]", "", value.lower()).strip()


def match_member(record: HouseFilingRecord, members: Sequence[Member]) -> int | None:
    """Match an index record to a Member id, or None when not confidently matched.

    Deterministic rule: same normalized last name AND same state, then the
    first whitespace token of the index first-name must equal the first token
    of the member's first name. Exactly one candidate is required — zero or
    many means unmatched (never force-linked).
    """
    state = record.state_district[:2]
    last = _normalize(record.last_name)
    first_token = _normalize(record.first_name).split(" ")[0] if record.first_name else ""

    matches = [
        m
        for m in members
        if m.chamber == Chamber.HOUSE  # the House index only contains House filers
        and m.state == state
        and _normalize(m.last_name) == last
        and first_token
        and _normalize(m.first_name).split(" ")[0] == first_token
    ]
    if len(matches) == 1:
        return matches[0].id
    return None


def upsert_filings(
    session: Session, records: list[HouseFilingRecord], source_url: str
) -> Counters:
    """Upsert filings deduplicated on official_doc_id; log unmatched-filer count."""
    counters = Counters(total=len(records))
    house_members = session.exec(select(Member).where(Member.chamber == Chamber.HOUSE)).all()
    unmatched = 0
    for record in records:
        member_id = match_member(record, house_members)
        if member_id is None:
            unmatched += 1
        existing = session.exec(
            select(Filing).where(
                Filing.chamber == Chamber.HOUSE,
                Filing.official_doc_id == record.doc_id,
            )
        ).first()
        values = {
            "member_id": member_id,
            "filing_type": _FILING_TYPE_MAP.get(record.filing_type_raw, FilingType.OTHER),
            "filing_type_raw": record.filing_type_raw,
            "filer_name": record.filer_name,
            "state_district": record.state_district,
            "index_year": record.index_year,
            "filing_date": record.filing_date,
            "source_url": source_url,
        }
        if existing is None:
            session.add(Filing(chamber=Chamber.HOUSE, official_doc_id=record.doc_id, **values))
            counters.new += 1
        elif any(getattr(existing, field) != value for field, value in values.items()):
            for field, value in values.items():
                setattr(existing, field, value)
            counters.changed += 1
        else:
            counters.unchanged += 1
    logger.info("filings matched to members: %d; unmatched (recorded unlinked): %d",
                counters.total - unmatched, unmatched)
    return counters
