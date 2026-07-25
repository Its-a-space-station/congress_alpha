"""M2b tests: House PTR parser golden set, controls, cross-checks, storage.

Golden fixtures are real Clerk PTR PDFs with agent-audited expected parses
(see tasks/todo.md M2b). The fail2pass test proves the golden comparison
actually discriminates; negative controls prove junk yields zero rows.
"""

import copy
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from app.core.enums import Chamber, FilingType
from app.db.models import Asset, Filing, Transaction
from app.db.session import get_engine
from app.parsing.house_ptr import PARSER_VERSION, cross_check, parse_ptr_pdf
from app.parsing.store import store_result

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ptr"
NOT_A_PTR = Path(__file__).parent / "fixtures" / "not_a_ptr.pdf"
GOLDEN_DOC_IDS = [
    "20026537",
    "20026727",
    "20030397",
    "20030502",
    "20032052",
    "20032062",
]
SOURCE_URL = "https://example.test/filing"


def _load_expected(doc_id: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{doc_id}.expected.json").read_text())


def _tx_as_dict(tx: object) -> dict:
    """Render a ParsedTransaction exactly as the expected-JSON shape."""
    from app.parsing.house_ptr import ParsedTransaction

    assert isinstance(tx, ParsedTransaction)
    return {
        "owner": tx.owner.value,
        "asset_name": tx.asset_name,
        "ticker": tx.ticker,
        "asset_type_code": tx.asset_type_code,
        "transaction_type": tx.transaction_type.value,
        "transaction_date": str(tx.transaction_date),
        "notification_date": str(tx.notification_date),
        "amount_min": str(tx.amount_min) if tx.amount_min is not None else None,
        "amount_max": str(tx.amount_max) if tx.amount_max is not None else None,
        "parse_confidence": tx.parse_confidence,
        "warnings": list(tx.warnings),
    }


@pytest.mark.parametrize("doc_id", GOLDEN_DOC_IDS)
def test_golden_parse_matches_expected(doc_id: str) -> None:
    expected = _load_expected(doc_id)
    result = parse_ptr_pdf(FIXTURE_DIR / f"{doc_id}.pdf")

    assert result.filer_name == expected["filer_name"]
    assert result.state_district == expected["state_district"]
    assert [_tx_as_dict(tx) for tx in result.transactions] == expected["transactions"]


def test_golden_comparison_discriminates_fail2pass() -> None:
    # A corrupted expectation MUST fail the golden comparison — otherwise the
    # golden tests are vacuous.
    expected = _load_expected("20032062")
    result = parse_ptr_pdf(FIXTURE_DIR / "20032062.pdf")
    corrupted = copy.deepcopy(expected)
    corrupted["transactions"][0]["ticker"] = "FAKE"
    assert [_tx_as_dict(tx) for tx in result.transactions] != corrupted["transactions"]


def test_garbage_pdf_yields_zero_rows_no_crash(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"this is not a pdf at all")
    result = parse_ptr_pdf(garbage)
    assert result.transactions == ()
    assert len(result.warnings) > 0


def test_non_ptr_document_yields_zero_rows() -> None:
    result = parse_ptr_pdf(NOT_A_PTR)
    assert result.transactions == ()
    assert "not a PTR document" in result.warnings


def test_cross_check_flags_source_date_anomaly() -> None:
    # 20026537 contains two source-typo notification dates (01/08/2024 before
    # the 12/03/2024 transaction dates); the check must surface them.
    result = parse_ptr_pdf(FIXTURE_DIR / "20026537.pdf")
    warnings = cross_check(result)
    assert sum("notification date" in w for w in warnings) == 2


def test_cross_check_flags_transaction_after_filing() -> None:
    result = parse_ptr_pdf(FIXTURE_DIR / "20032062.pdf")
    warnings = cross_check(result, filing_date=date(2025, 1, 1))
    assert any("after filing date" in w for w in warnings)


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_filing(session: Session, doc_id: str) -> Filing:
    filing = Filing(
        chamber=Chamber.HOUSE,
        filing_type=FilingType.PERIODIC_TRANSACTION,
        filing_type_raw="P",
        official_doc_id=doc_id,
        index_year=2025,
        filing_date=date(2025, 9, 10),
        filer_name="Robert B. Aderholt",
        source_url=SOURCE_URL,
    )
    session.add(filing)
    session.flush()
    return filing


def test_store_result_is_idempotent_and_version_gated(session: Session) -> None:
    filing = _make_filing(session, "20032062")
    result = parse_ptr_pdf(FIXTURE_DIR / "20032062.pdf")

    first = store_result(session, filing, result)
    assert first.new == 1
    assert filing.parser_version == PARSER_VERSION

    second = store_result(session, filing, result)
    assert (second.new, second.unchanged) == (0, 1)

    # Simulate a parser bump: rows are replaced, not duplicated.
    import app.parsing.store as store_module

    original = store_module.PARSER_VERSION
    try:
        store_module.PARSER_VERSION = "house-ptr-0.2"
        third = store_result(session, filing, result)
        assert third.new == 1
        rows = session.exec(select(Transaction).where(Transaction.filing_id == filing.id)).all()
        assert len(rows) == 1
        assert rows[0].parser_version == "house-ptr-0.2"
    finally:
        store_module.PARSER_VERSION = original


def test_store_result_dedupes_assets_verbatim(session: Session) -> None:
    first_filing = _make_filing(session, "20032052")
    second_filing = _make_filing(session, "20032204")
    store_result(session, first_filing, parse_ptr_pdf(FIXTURE_DIR / "20032052.pdf"))
    store_result(session, second_filing, parse_ptr_pdf(FIXTURE_DIR / "20032204.pdf"))

    rows = session.exec(select(Transaction)).all()
    assert len(rows) == 4  # 3 + 1 transactions across the two filings

    assets = session.exec(select(Asset)).all()
    # The ACN sale appears in both filings with identical verbatim name+ticker
    # -> one shared Asset row, so 4 transactions map to 3 assets.
    assert len(assets) == 3
    acn = next(a for a in assets if a.ticker == "ACN")
    assert acn.name == "Accenture plc Class A Ordinary Shares"
    treasury = next(a for a in assets if a.ticker is None)
    assert treasury.asset_type.value == "other"  # [GS] is not a stock

    assert all(row.parser_version == PARSER_VERSION for row in rows)
    assert all(row.raw_text for row in rows)  # raw text always preserved
