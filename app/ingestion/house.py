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
    disclosure_type: str | None  # <DisclosureType>: only in early index years
    state_district: str
    index_year: int
    filing_date: date | None
    doc_id: str

    @property
    def filer_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def _effective_filing_type(record: HouseFilingRecord) -> FilingType:
    """Map a record to its FilingType, handling the early index scheme.

    In the 2012-2013 index files PTRs are coded FilingType=O with
    DisclosureType=PTR (verified 2026-07-25); from 2015 they carry FilingType=P.
    The raw code is always preserved on the Filing row.
    """
    if record.filing_type_raw == "O" and record.disclosure_type == "PTR":
        return FilingType.PERIODIC_TRANSACTION
    return _FILING_TYPE_MAP.get(record.filing_type_raw, FilingType.OTHER)


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
                disclosure_type=_text(member, "DisclosureType"),
                state_district=state_district,
                index_year=int(year_text),
                filing_date=_parse_filing_date(_text(member, "FilingDate")),
                doc_id=doc_id,
            )
        )
    return records, skipped


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z ]", "", value.lower()).strip()


# Curated nickname groups for filer-name matching (index names are often the
# formal variant, e.g. "Richard W. Allen" vs dataset first-name "Rick").
# Reviewable plain data; extend only with common English given-name groups.
_NICKNAME_GROUPS = [
    {"richard", "rick", "ricky", "dick", "rich"},
    {"william", "bill", "billy", "will", "liam"},
    {"robert", "bob", "bobby", "rob", "robby"},
    {"james", "jim", "jimmy", "jamie"},
    {"michael", "mike", "micky"},
    {"john", "jack", "johnny", "jon"},
    {"charles", "chuck", "charlie", "chas"},
    {"thomas", "tom", "tommy"},
    {"joseph", "joe", "joey"},
    {"donald", "don", "donny"},
    {"edward", "ed", "eddie", "ted", "teddy"},
    {"katherine", "kathryn", "kate", "katie", "kathy", "catherine", "cathy"},
    {"elizabeth", "liz", "beth", "betty", "betsy"},
    {"margaret", "maggie", "meg", "peggy"},
    {"patricia", "pat", "patty", "tricia"},
    {"susan", "sue", "susie", "suzy"},
    {"daniel", "dan", "danny"},
    {"david", "dave", "davey"},
    {"stephen", "steven", "steve"},
    {"anthony", "tony"},
    {"andrew", "andy", "drew"},
    {"nicholas", "nick", "nicky"},
    {"christopher", "chris", "topher"},
    {"matthew", "matt", "matty"},
    {"jonathan", "jon", "jonny"},
    {"benjamin", "ben", "benny"},
    {"samuel", "sam", "sammy"},
    {"gregory", "greg"},
    {"jeffrey", "jeff", "geoffrey"},
    {"kenneth", "ken", "kenny"},
    {"jacob", "jake"},
    {"zachary", "zach", "zack"},
    {"joshua", "josh"},
    {"ronald", "ron", "ronnie"},
    {"timothy", "tim", "timmy"},
    {"douglas", "doug"},
    {"gary", "garry"},
    {"lawrence", "larry"},
    {"gerald", "jerry", "gerry"},
    {"walter", "walt", "wally"},
    {"henry", "hank", "harry"},
    {"frank", "frankie", "francis"},
    {"raymond", "ray"},
    {"peter", "pete"},
    {"paul", "paulie"},
    {"mark", "marc"},
    {"virginia", "ginny", "ginger"},
    {"deborah", "debra", "deb", "debbie"},
    {"barbara", "barb", "barbie"},
    {"jennifer", "jen", "jenny"},
    {"nancy", "nan"},
    {"carolyn", "caroline", "carol"},
    {"judith", "judy"},
    {"linda", "lindy"},
    {"sandra", "sandy"},
    {"pamela", "pam"},
    {"rebecca", "becky"},
    {"victoria", "vicky", "vicki"},
    {"christina", "christine", "chris", "christy", "tina"},
    {"michelle", "shelly"},
    {"amanda", "mandy"},
    {"angela", "angie"},
]
_TOKEN_TO_GROUP: dict[str, frozenset[str]] = {}
for _group in _NICKNAME_GROUPS:
    _frozen = frozenset(_group)
    for _token in _group:
        _TOKEN_TO_GROUP[_token] = _frozen


def _tokens_equivalent(index_token: str, member_token: str) -> bool:
    """First-name token equivalence: exact match or same nickname group."""
    if index_token == member_token:
        return True
    group = _TOKEN_TO_GROUP.get(index_token)
    return group is not None and member_token in group


def _name_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [token for token in _normalize(value).split(" ") if token]


def match_member(record: HouseFilingRecord, members: Sequence[Member]) -> int | None:
    """Match an index record to a Member id, or None when not confidently matched.

    Deterministic rule chain (House members only, same normalized last name,
    same state; the index first-name token must match ONE of):
    1. the member's first-name token (exact);
    2. any official_full token (exact);
    3. any of the above via the curated nickname groups.
    Exactly one candidate member is required — zero or many means unmatched
    (never force-linked).
    """
    state = record.state_district[:2]
    last = _normalize(record.last_name)
    index_tokens = _name_tokens(record.first_name)
    index_first = index_tokens[0] if index_tokens else ""

    matches = []
    for member in members:
        if member.chamber != Chamber.HOUSE or member.state != state:
            continue
        if _normalize(member.last_name) != last or not index_first:
            continue
        first_tokens = _name_tokens(member.first_name)
        official_tokens = _name_tokens(member.official_full)
        exact_hit = index_first in first_tokens or index_first in official_tokens
        nickname_hit = any(
            _tokens_equivalent(index_first, token)
            for token in [*first_tokens, *official_tokens]
        )
        if exact_hit or nickname_hit:
            matches.append(member)
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
            "filing_type": _effective_filing_type(record),
            "filing_type_raw": record.filing_type_raw,
            "disclosure_type": record.disclosure_type,
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
