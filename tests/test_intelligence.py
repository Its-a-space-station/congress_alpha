"""M3 tests: golden reconstruction scenarios, normalization, net worth.

Each scenario builds a small synthetic in-memory DB and checks the exact
Position/NetWorthEstimate output — the reconstruction method's contract.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, SQLModel, select

from app.core.enums import (
    CertaintyLabel,
    Chamber,
    FilingType,
    OwnerType,
    TransactionType,
)
from app.db.models import (
    Asset,
    Filing,
    Holding,
    Liability,
    Member,
    Position,
    Transaction,
)
from app.db.session import get_engine
from app.intelligence.checkpoint import check_member
from app.intelligence.net_worth import estimate_net_worth
from app.intelligence.normalize import asset_group_key, canonical_asset_key, midpoint
from app.intelligence.positions import (
    METHOD_BASELINE,
    METHOD_CORROBORATED,
    METHOD_MAYBE_REDUCED,
    METHOD_PTR_NEW,
    reconstruct_member,
)


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _member(session: Session) -> Member:
    member = Member(
        bioguide_id="T000001",
        first_name="Test",
        last_name="Member",
        chamber=Chamber.HOUSE,
        party="Independent",
        state="CA",
        district="1",
    )
    session.add(member)
    session.flush()
    return member


def _filing(session: Session, member: Member, type_raw: str, doc: str) -> Filing:
    filing = Filing(
        chamber=Chamber.HOUSE,
        member_id=member.id,
        filing_type=FilingType.ANNUAL if type_raw == "A" else FilingType.PERIODIC_TRANSACTION,
        filing_type_raw=type_raw,
        official_doc_id=doc,
        index_year=2025,
        filing_date=date(2026, 5, 15) if type_raw == "A" else date(2026, 6, 1),
        source_url="https://example.test",
    )
    session.add(filing)
    session.flush()
    return filing


def _asset(session: Session, name: str, ticker: str | None) -> Asset:
    asset = Asset(name=name, ticker=ticker)
    session.add(asset)
    session.flush()
    return asset


def _holding(
    session: Session, filing: Filing, asset: Asset, vmin: str | None, vmax: str | None
) -> None:
    session.add(
        Holding(
            filing_id=filing.id,
            asset_id=asset.id,
            owner=OwnerType.SELF,
            value_min=Decimal(vmin) if vmin else None,
            value_max=Decimal(vmax) if vmax else None,
            raw_text="raw",
            parse_confidence=1.0,
            parser_version="test",
        )
    )


def _tx(
    session: Session,
    filing: Filing,
    asset: Asset,
    kind: TransactionType,
    day: date,
    amin: str | None,
    amax: str | None,
    raw: str = "raw",
) -> None:
    session.add(
        Transaction(
            filing_id=filing.id,
            asset_id=asset.id,
            owner=OwnerType.SELF,
            transaction_type=kind,
            transaction_date=day,
            amount_min=Decimal(amin) if amin else None,
            amount_max=Decimal(amax) if amax else None,
            raw_text=raw,
            parse_confidence=1.0,
            parser_version="test",
        )
    )


def _positions(session: Session, member: Member) -> list[Position]:
    report = check_member(session, member.id)  # type: ignore[arg-type]
    reconstruct_member(session, member.id, report)  # type: ignore[arg-type]
    return session.exec(select(Position).where(Position.member_id == member.id)).all()


def test_midpoint_math() -> None:
    assert midpoint(Decimal("1001"), Decimal("15000")) == Decimal("8000.50")
    assert midpoint(Decimal("1001"), None) is None
    assert midpoint(None, Decimal("15000")) is None


def test_asset_grouping_confidence() -> None:
    assert canonical_asset_key("Apple Inc. - Common Stock") == "apple inc common stock"
    key_ticker, conf_ticker = asset_group_key("Apple Inc.", "AAPL")
    assert (key_ticker, conf_ticker) == ("ticker:AAPL", CertaintyLabel.HIGH)
    key_name, conf_name = asset_group_key("Apple Inc. - Common Stock", None)
    assert key_name == "name:apple inc common stock"
    assert conf_name is CertaintyLabel.MEDIUM


def test_scenario_baseline_only(session: Session) -> None:
    member = _member(session)
    fd = _filing(session, member, "A", "FD1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    cash = _asset(session, "Checking Account", None)
    _holding(session, fd, apple, "15001", "50000")
    _holding(session, fd, cash, None, None)  # disclosed None

    positions = _positions(session, member)
    assert len(positions) == 2
    apple_pos = next(p for p in positions if p.asset_id == apple.id)
    assert apple_pos.method == METHOD_BASELINE
    assert apple_pos.certainty is CertaintyLabel.MEDIUM
    assert apple_pos.value_min == Decimal("15001")
    assert apple_pos.value_midpoint == Decimal("32500.50")
    cash_pos = next(p for p in positions if p.asset_id == cash.id)
    assert cash_pos.value_min is None  # disclosed None stays rangeless


def test_scenario_purchase_after_baseline(session: Session) -> None:
    member = _member(session)
    fd = _filing(session, member, "A", "FD1")
    ptr = _filing(session, member, "P", "PTR1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    netflix = _asset(session, "Netflix, Inc. - Common Stock", "NFLX")
    _holding(session, fd, apple, "15001", "50000")
    _tx(session, ptr, apple, TransactionType.PURCHASE, date(2026, 6, 10), "1001", "15000")
    _tx(session, ptr, netflix, TransactionType.PURCHASE, date(2026, 6, 10), "15001", "50000")

    positions = _positions(session, member)
    assert len(positions) == 2
    apple_pos = next(p for p in positions if p.asset_id == apple.id)
    assert apple_pos.method == METHOD_CORROBORATED
    assert apple_pos.certainty is CertaintyLabel.MEDIUM
    nflx_pos = next(p for p in positions if p.asset_id == netflix.id)
    assert nflx_pos.method == METHOD_PTR_NEW
    assert nflx_pos.certainty is CertaintyLabel.LOW
    assert nflx_pos.value_min == Decimal("15001")
    assert nflx_pos.value_max == Decimal("50000")


def test_scenario_full_sale(session: Session) -> None:
    member = _member(session)
    fd = _filing(session, member, "A", "FD1")
    ptr = _filing(session, member, "P", "PTR1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    _holding(session, fd, apple, "15001", "50000")
    _tx(session, ptr, apple, TransactionType.SALE, date(2026, 6, 10), "15001", "50000")

    positions = _positions(session, member)
    assert len(positions) == 1
    assert positions[0].method == METHOD_MAYBE_REDUCED
    assert positions[0].certainty is CertaintyLabel.LOW


def test_scenario_partial_sale(session: Session) -> None:
    member = _member(session)
    fd = _filing(session, member, "A", "FD1")
    ptr = _filing(session, member, "P", "PTR1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    _holding(session, fd, apple, "15001", "50000")
    _tx(
        session,
        ptr,
        apple,
        TransactionType.SALE,
        date(2026, 6, 10),
        "15001",
        "50000",
        raw="Apple Inc. - Common Stock (AAPL) S (partial) 06/10/2026 $15,001 - $50,000",
    )

    positions = _positions(session, member)
    assert len(positions) == 1
    assert "partial sale noted" in positions[0].method
    assert positions[0].certainty is CertaintyLabel.LOW


def test_scenario_range_only_data(session: Session) -> None:
    member = _member(session)
    ptr = _filing(session, member, "P", "PTR1")
    bond = _asset(session, "US Treasury Bill 91282CHB0", None)
    _tx(session, ptr, bond, TransactionType.PURCHASE, date(2026, 6, 10), "100001", None)

    positions = _positions(session, member)
    assert len(positions) == 1
    # Open-range purchase: presence recorded, no dishonest upper bound.
    assert positions[0].method == METHOD_PTR_NEW
    assert positions[0].value_min is None
    assert positions[0].value_max is None


def test_scenario_missing_baseline(session: Session) -> None:
    member = _member(session)
    ptr = _filing(session, member, "P", "PTR1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    _tx(session, ptr, apple, TransactionType.PURCHASE, date(2026, 6, 10), "1001", "15000")

    report = check_member(session, member.id)  # type: ignore[arg-type]
    assert report.fd_filing_id is None
    assert "no annual FD" in report.warnings[0]
    positions = _positions(session, member)
    assert len(positions) == 1
    assert positions[0].method == METHOD_PTR_NEW
    assert positions[0].certainty is CertaintyLabel.LOW
    assert estimate_net_worth(session, report) is None


def test_net_worth_household_sums_and_certainty(session: Session) -> None:
    member = _member(session)
    fd = _filing(session, member, "A", "FD1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    fund = _asset(session, "Index Fund", None)
    session.add(
        Holding(
            filing_id=fd.id,
            asset_id=apple.id,
            owner=OwnerType.SELF,
            value_min=Decimal("15001"),
            value_max=Decimal("50000"),
            raw_text="raw",
            parse_confidence=1.0,
            parser_version="test",
        )
    )
    session.add(
        Holding(
            filing_id=fd.id,
            asset_id=fund.id,
            owner=OwnerType.SPOUSE,  # household member counts too
            value_min=Decimal("50001"),
            value_max=Decimal("100000"),
            raw_text="raw",
            parse_confidence=1.0,
            parser_version="test",
        )
    )
    session.add(
        Liability(
            filing_id=fd.id,
            owner=OwnerType.JOINT,
            creditor_name="Bank",
            value_min=Decimal("50001"),
            value_max=Decimal("100000"),
            raw_text="raw",
            parse_confidence=1.0,
            parser_version="test",
        )
    )

    report = check_member(session, member.id)  # type: ignore[arg-type]
    estimate = estimate_net_worth(session, report)
    assert estimate is not None
    # min = (15001+50001) - 100000 = -34998; max = (50000+100000) - 50001 = 99999
    assert estimate.estimate_min == Decimal("-34998")
    assert estimate.estimate_max == Decimal("99999")
    assert estimate.estimate_midpoint == Decimal("32500.50")
    assert estimate.certainty is CertaintyLabel.HIGH
    assert estimate.year == 2025


def test_rebuild_is_deterministic(session: Session) -> None:
    member = _member(session)
    fd = _filing(session, member, "A", "FD1")
    apple = _asset(session, "Apple Inc. - Common Stock", "AAPL")
    _holding(session, fd, apple, "15001", "50000")

    first = _positions(session, member)
    session.commit()
    # Rebuild semantics: delete derived rows, recompute, compare.
    for row in session.exec(select(Position)).all():
        session.delete(row)
    second = _positions(session, member)
    assert [(p.asset_id, p.value_min, p.certainty) for p in first] == [
        (p.asset_id, p.value_min, p.certainty) for p in second
    ]
