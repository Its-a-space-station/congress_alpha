"""M2c tests: annual-FD parser golden set, controls, cross-checks, storage.

Golden fixtures are real annual FD PDFs with agent-audited expected parses,
covering: the electronic form, the two-income-column variant, account
wrappers, mid-page sections, wrapped liability amounts, and None-disclosed
sections.
"""

import copy
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from app.core.enums import Chamber, FilingType
from app.db.models import Asset, Filing, Holding, Liability
from app.db.session import get_engine
from app.parsing.house_fd import PARSER_VERSION, cross_check_fd, parse_fd_pdf
from app.parsing.store_fd import store_fd_result

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fd"
PTR_FIXTURE = Path(__file__).parent / "fixtures" / "ptr" / "20032062.pdf"
GOLDEN_DOC_IDS = [
    "10071624",
    "10071993",
    "10072736",
    "10073311",
    "10074393",
    "10079783",
]
SOURCE_URL = "https://example.test/filing"


def _load_expected(doc_id: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{doc_id}.expected.json").read_text())


def _holding_as_dict(h: object) -> dict:
    from app.parsing.house_fd import ParsedHolding

    assert isinstance(h, ParsedHolding)
    return {
        "account": h.account,
        "owner": h.owner.value,
        "asset_name": h.asset_name,
        "ticker": h.ticker,
        "asset_type_code": h.asset_type_code,
        "value_min": str(h.value_min) if h.value_min is not None else None,
        "value_max": str(h.value_max) if h.value_max is not None else None,
        "income_details": h.income_details,
        "description": h.description,
        "location": h.location,
        "parse_confidence": h.parse_confidence,
        "warnings": list(h.warnings),
    }


def _liability_as_dict(liability: object) -> dict:
    from app.parsing.house_fd import ParsedLiability

    assert isinstance(liability, ParsedLiability)
    return {
        "owner": liability.owner.value,
        "creditor_name": liability.creditor_name,
        "liability_type": liability.liability_type,
        "date_incurred": liability.date_incurred,
        "value_min": str(liability.value_min) if liability.value_min is not None else None,
        "value_max": str(liability.value_max) if liability.value_max is not None else None,
        "parse_confidence": liability.parse_confidence,
        "warnings": list(liability.warnings),
    }


@pytest.mark.parametrize("doc_id", GOLDEN_DOC_IDS)
def test_golden_fd_parse_matches_expected(doc_id: str) -> None:
    expected = _load_expected(doc_id)
    result = parse_fd_pdf(FIXTURE_DIR / f"{doc_id}.pdf")

    assert result.filer_name == expected["filer_name"]
    assert result.state_district == expected["state_district"]
    assert [_holding_as_dict(h) for h in result.holdings] == expected["holdings"]
    assert [_liability_as_dict(row) for row in result.liabilities] == expected["liabilities"]


def test_golden_comparison_discriminates_fail2pass() -> None:
    expected = _load_expected("10073311")
    result = parse_fd_pdf(FIXTURE_DIR / "10073311.pdf")
    corrupted = copy.deepcopy(expected)
    corrupted["holdings"][0]["value_min"] = "999"
    assert [_holding_as_dict(h) for h in result.holdings] != corrupted["holdings"]


def test_garbage_pdf_yields_zero_rows_no_crash(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"definitely not a pdf")
    result = parse_fd_pdf(garbage)
    assert result.holdings == []
    assert result.liabilities == []
    assert len(result.warnings) > 0


def test_ptr_document_is_not_an_fd() -> None:
    result = parse_fd_pdf(PTR_FIXTURE)
    assert result.holdings == []
    assert result.liabilities == []
    assert "not an annual FD document" in result.warnings


def test_cross_check_fd_flags_inverted_ranges() -> None:
    import dataclasses

    from app.parsing.house_fd import FdParseResult

    result = parse_fd_pdf(FIXTURE_DIR / "10074393.pdf")
    inverted = dataclasses.replace(
        result.holdings[0],
        value_min=result.holdings[0].value_max,
        value_max=result.holdings[0].value_min,
    )
    swapped = FdParseResult(
        filer_name=result.filer_name,
        state_district=result.state_district,
        holdings=[inverted, *result.holdings[1:]],
        liabilities=result.liabilities,
        warnings=result.warnings,
    )
    warnings = cross_check_fd(swapped)
    assert any("min" in w and "max" in w for w in warnings)


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_fd_filing(session: Session, doc_id: str) -> Filing:
    filing = Filing(
        chamber=Chamber.HOUSE,
        filing_type=FilingType.ANNUAL,
        filing_type_raw="A",
        official_doc_id=doc_id,
        index_year=2025,
        filing_date=date(2026, 6, 6),
        filer_name="Donald J. Bacon",
        source_url=SOURCE_URL,
    )
    session.add(filing)
    session.flush()
    return filing


def test_store_fd_result_is_idempotent_and_version_gated(session: Session) -> None:
    filing = _make_fd_filing(session, "10079783")
    result = parse_fd_pdf(FIXTURE_DIR / "10079783.pdf")
    total_rows = len(result.holdings) + len(result.liabilities)

    first = store_fd_result(session, filing, result)
    assert first.new == total_rows
    assert filing.parser_version == PARSER_VERSION

    second = store_fd_result(session, filing, result)
    assert (second.new, second.unchanged) == (0, total_rows)

    import app.parsing.store_fd as store_module

    original = store_module.PARSER_VERSION
    try:
        store_module.PARSER_VERSION = "house-fd-0.2"
        third = store_fd_result(session, filing, result)
        assert third.new == total_rows
        assert (
            session.exec(select(Holding).where(Holding.filing_id == filing.id)).all()[
                0
            ].parser_version
            == "house-fd-0.2"
        )
    finally:
        store_module.PARSER_VERSION = original


def test_store_fd_result_links_assets_and_liabilities(session: Session) -> None:
    filing = _make_fd_filing(session, "10074393")
    result = parse_fd_pdf(FIXTURE_DIR / "10074393.pdf")
    store_fd_result(session, filing, result)

    holdings = session.exec(select(Holding)).all()
    liabilities = session.exec(select(Liability)).all()
    assert len(holdings) == len(result.holdings)
    assert len(liabilities) == len(result.liabilities) == 3

    named = [h for h in holdings if h.asset_id is not None]
    assert named  # most holdings link to an asset
    asset_ids = {h.asset_id for h in named}
    assert len(asset_ids) == len(session.exec(select(Asset)).all())
    assert all(h.raw_text for h in holdings)  # raw text always preserved
    mortgage = next(row for row in liabilities if row.liability_type == "mortgage")
    assert mortgage.creditor_name == "Freedom Mortgage"
    assert mortgage.value_min is not None
