"""M2a tests: House index parsing, member matching, filing dedupe, downloader.

The fixture is a real sample of the Clerk's 2025FD.xml index; the duplicated
Aderholt record is intentional — it exercises within-file doc-id dedupe.
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from app.core.enums import Chamber, FilingType
from app.db.models import Filing, Member
from app.db.session import get_engine
from app.ingestion.downloads import download_filings
from app.ingestion.house import (
    HouseFilingRecord,
    match_member,
    parse_index,
    upsert_filings,
)
from app.ingestion.http import sha256_bytes
from app.ingestion.loaders import table_counts

FIXTURE = Path(__file__).parent / "fixtures" / "house_index_sample.xml"
SOURCE_URL = "https://example.test/2025FD.zip"


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_members(session: Session) -> Member:
    aderholt = Member(
        bioguide_id="A000055",
        first_name="Robert",
        last_name="Aderholt",
        official_full="Robert B. Aderholt",
        chamber=Chamber.HOUSE,
        party="Republican",
        state="AL",
        district="4",
    )
    allen = Member(
        bioguide_id="A000372",
        first_name="Rick",
        last_name="Allen",
        official_full="Rick W. Allen",
        chamber=Chamber.HOUSE,
        party="Republican",
        state="GA",
        district="12",
    )
    cantwell = Member(
        bioguide_id="C000127",
        first_name="Maria",
        last_name="Cantwell",
        chamber=Chamber.SENATE,
        party="Democrat",
        state="WA",
    )
    session.add(aderholt)
    session.add(allen)
    session.add(cantwell)
    session.flush()
    return aderholt


def test_parse_index_real_sample() -> None:
    records, skipped = parse_index(FIXTURE)

    assert skipped == 0
    assert len(records) == 7

    aderholt = next(r for r in records if r.doc_id == "20032062")
    assert aderholt.filing_type_raw == "P"
    assert aderholt.state_district == "AL04"
    assert aderholt.index_year == 2025
    assert aderholt.filing_date == date(2025, 9, 10)
    assert aderholt.filer_name == "Robert B. Aderholt"

    annual = next(r for r in records if r.doc_id == "10073311")
    assert annual.filing_type_raw == "A"
    assert annual.filing_date == date(2026, 2, 2)  # index year can differ from file year


def test_parse_index_skips_malformed(tmp_path: Path) -> None:
    xml = tmp_path / "bad.xml"
    xml.write_text(
        """<?xml version="1.0"?>
<FinancialDisclosure>
  <Member><Last>Good</Last><First>One</First><FilingType>A</FilingType>
    <StateDst>CA01</StateDst><Year>2025</Year><FilingDate>1/1/2025</FilingDate>
    <DocID>111</DocID></Member>
  <Member><Last>Broken</Last><FilingType>A</FilingType>
    <StateDst>CA01</StateDst><Year>2025</Year></Member>
</FinancialDisclosure>
"""
    )
    records, skipped = parse_index(xml)
    assert len(records) == 1
    assert skipped == 1


def _record(last: str, first: str, state_dst: str) -> HouseFilingRecord:
    return HouseFilingRecord(
        last_name=last,
        first_name=first,
        prefix=None,
        suffix=None,
        filing_type_raw="P",
        disclosure_type=None,
        state_district=state_dst,
        index_year=2025,
        filing_date=None,
        doc_id="x",
    )


def test_early_scheme_disclosure_type_mapping(tmp_path: Path, session: Session) -> None:
    """2012-2013 scheme: FilingType=O + DisclosureType=PTR -> PERIODIC_TRANSACTION."""
    xml = tmp_path / "early.xml"
    xml.write_text(
        """<?xml version="1.0"?>
<FinancialDisclosure>
  <Member><Last>Early</Last><First>Era</First><FilingType>O</FilingType>
    <StateDst>CA01</StateDst><Year>2012</Year><FilingDate>5/1/2012</FilingDate>
    <DocID>2000001</DocID><DisclosureType>PTR</DisclosureType></Member>
  <Member><Last>Plain</Last><First>Other</First><FilingType>O</FilingType>
    <StateDst>CA01</StateDst><Year>2012</Year><FilingDate>5/1/2012</FilingDate>
    <DocID>2000002</DocID><DisclosureType>FD</DisclosureType></Member>
</FinancialDisclosure>
"""
    )
    records, skipped = parse_index(xml)
    assert skipped == 0
    assert len(records) == 2
    assert records[0].disclosure_type == "PTR"

    upsert_filings(session, records, SOURCE_URL)
    by_doc = {f.official_doc_id: f for f in session.exec(select(Filing)).all()}
    # O + PTR maps to PERIODIC_TRANSACTION with the raw code preserved...
    assert by_doc["2000001"].filing_type is FilingType.PERIODIC_TRANSACTION
    assert by_doc["2000001"].filing_type_raw == "O"
    assert by_doc["2000001"].disclosure_type == "PTR"
    # ...while O + FD stays OTHER with its disclosure type preserved.
    assert by_doc["2000002"].filing_type is FilingType.OTHER
    assert by_doc["2000002"].disclosure_type == "FD"


def test_downloader_excludes_pre_2015_documents(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def fake_fetch(url: str, cache_path: Path, *, refresh: bool = False) -> bytes:
        called.append(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"%PDF-fake")
        return b"%PDF-fake"

    monkeypatch.setattr("app.ingestion.downloads.fetch", fake_fetch)
    monkeypatch.setattr("app.ingestion.downloads.PACE_SECONDS", 0)

    session.add(
        Filing(
            chamber=Chamber.HOUSE,
            filing_type=FilingType.PERIODIC_TRANSACTION,
            filing_type_raw="P",
            official_doc_id="NEW2015",
            index_year=2015,
            source_url=SOURCE_URL,
        )
    )
    session.add(
        Filing(
            chamber=Chamber.HOUSE,
            filing_type=FilingType.PERIODIC_TRANSACTION,
            filing_type_raw="O",
            disclosure_type="PTR",
            official_doc_id="OLD2012",
            index_year=2012,
            source_url=SOURCE_URL,
        )
    )
    session.flush()

    counters = download_filings(session, tmp_path, limit=10)
    assert (counters.new, counters.skipped) == (1, 0)
    assert len(called) == 1  # the 2012 document was never network-attempted
    old = session.exec(select(Filing).where(Filing.official_doc_id == "OLD2012")).one()
    assert old.local_path is None


def test_match_member_rules(session: Session) -> None:
    aderholt = _seed_members(session)
    allen = session.exec(select(Member).where(Member.bioguide_id == "A000372")).one()
    members = session.exec(select(Member)).all()

    # index "Robert B." matches member first-name token "Robert"
    assert match_member(_record("Aderholt", "Robert B.", "AL04"), members) == aderholt.id
    # nickname: index "Richard W." matches member first-name "Rick"
    assert match_member(_record("Allen", "Richard W.", "GA12"), members) == allen.id
    # unknown person -> unmatched
    assert match_member(_record("Aaron", "Richard", "MI04"), members) is None
    # senators are never matched in the House index, even with a name hit
    assert match_member(_record("Cantwell", "Maria", "WA98"), members) is None
    # same last name + state but unrelated first-name token -> unmatched
    assert match_member(_record("Aderholt", "Julius", "AL04"), members) is None


def test_match_member_requires_unique_candidate(session: Session) -> None:
    _seed_members(session)
    session.add(
        Member(
            bioguide_id="X999999",
            first_name="Ricky",
            last_name="Allen",
            chamber=Chamber.HOUSE,
            party="Democrat",
            state="GA",
            district="1",
        )
    )
    session.flush()
    members = session.exec(select(Member)).all()
    # "Richard" nickname-matches both Rick and Ricky -> ambiguous -> unmatched
    assert match_member(_record("Allen", "Richard", "GA12"), members) is None


def test_upsert_filings_dedupes_and_preserves_raw(session: Session) -> None:
    aderholt = _seed_members(session)
    records, _ = parse_index(FIXTURE)

    first = upsert_filings(session, records, SOURCE_URL)
    # 7 records but doc id 20032062 appears twice: 6 new + 1 unchanged.
    assert (first.new, first.changed, first.unchanged) == (6, 0, 1)

    second = upsert_filings(session, records, SOURCE_URL)
    assert (second.new, second.changed, second.unchanged) == (0, 0, 7)

    filings = session.exec(select(Filing)).all()
    assert len(filings) == 6
    by_doc = {f.official_doc_id: f for f in filings}
    assert by_doc["20032062"].filing_type is FilingType.PERIODIC_TRANSACTION
    assert by_doc["20032062"].member_id == aderholt.id
    assert by_doc["10073311"].filing_type is FilingType.ANNUAL
    # unmapped codes stay OTHER with the raw code preserved, never guessed
    assert by_doc["40003749"].filing_type is FilingType.OTHER
    assert by_doc["40003749"].filing_type_raw == "D"
    assert by_doc["8005"].filing_type_raw == "W"
    # unmatched filers are recorded but unlinked
    assert by_doc["10072640"].member_id is None
    assert table_counts(session)["filing"] == 6


def test_downloader_is_idempotent(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_bytes = b"%PDF-1.4 fake"
    monkeypatch.setattr("app.ingestion.downloads.PACE_SECONDS", 0)

    def fake_fetch(url: str, cache_path: Path, *, refresh: bool = False) -> bytes:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(pdf_bytes)
        return pdf_bytes

    monkeypatch.setattr("app.ingestion.downloads.fetch", fake_fetch)

    session.add(
        Filing(
            chamber=Chamber.HOUSE,
            filing_type=FilingType.PERIODIC_TRANSACTION,
            official_doc_id="20032062",
            index_year=2025,
            source_url=SOURCE_URL,
        )
    )
    session.add(
        Filing(  # no doc id -> must be skipped, never crashes
            chamber=Chamber.HOUSE,
            filing_type=FilingType.OTHER,
            source_url=SOURCE_URL,
        )
    )
    session.flush()

    first = download_filings(session, tmp_path, limit=10)
    assert (first.new, first.skipped) == (1, 1)

    downloaded = session.exec(select(Filing).where(Filing.official_doc_id == "20032062")).one()
    assert downloaded.local_path is not None
    assert downloaded.checksum == sha256_bytes(pdf_bytes)
    assert Path(downloaded.local_path).read_bytes() == pdf_bytes

    second = download_filings(session, tmp_path, limit=10)
    assert (second.new, second.skipped) == (0, 1)  # only the broken row remains pending
