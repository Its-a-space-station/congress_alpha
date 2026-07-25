"""M4 tests: golden scoring scenario, decomposability, bands, notes.

Uses the REAL curated mapping files (they are the seeds under test). The
golden scenario asserts exact component values and that every component's
details string names its contributors.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, SQLModel, select

from app.core.enums import Chamber, FilingType, OwnerType, ScoreLabel, TransactionType
from app.db.models import (
    Asset,
    Committee,
    CommitteeMembership,
    Filing,
    Member,
    Position,
    ScoreComponent,
    Transaction,
)
from app.db.session import get_engine
from app.intelligence.mappings import load_mappings
from app.intelligence.notes import add_note, list_notes
from app.intelligence.scoring import DEFAULT_CONFIG, label_for, run_scoring, score_member

AS_OF = date(2026, 7, 25)


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _scenario(session: Session) -> Member:
    """Member on Armed Services (defense) holding + trading defense tickers."""
    member = Member(
        bioguide_id="T000002",
        first_name="Score",
        last_name="Case",
        chamber=Chamber.HOUSE,
        party="Independent",
        state="TX",
        district="1",
    )
    armed_services = Committee(
        code="SSAS", name="Senate Committee on Armed Services", chamber=Chamber.SENATE
    )
    agriculture = Committee(
        code="HSAG", name="House Committee on Agriculture", chamber=Chamber.HOUSE
    )
    session.add(member)
    session.add(armed_services)
    session.add(agriculture)
    session.flush()
    session.add(CommitteeMembership(member_id=member.id, committee_id=armed_services.id))
    session.add(CommitteeMembership(member_id=member.id, committee_id=agriculture.id))

    lmt = Asset(name="Lockheed Martin Corporation", ticker="LMT")
    gd = Asset(name="General Dynamics Corporation", ticker="GD")
    aapl = Asset(name="Apple Inc. - Common Stock", ticker="AAPL")
    for asset in (lmt, gd, aapl):
        session.add(asset)
    session.flush()

    # Holds LMT (defense overlap) and AAPL (no tech committee -> no overlap).
    for asset in (lmt, aapl):
        session.add(
            Position(
                member_id=member.id,
                asset_id=asset.id,
                as_of=date(2026, 6, 1),
                value_min=Decimal("15001"),
                value_max=Decimal("50000"),
                certainty="medium",
                method="test",
            )
        )

    filing = Filing(
        chamber=Chamber.HOUSE,
        member_id=member.id,
        filing_type=FilingType.PERIODIC_TRANSACTION,
        filing_type_raw="P",
        official_doc_id="PTRX",
        index_year=2026,
        source_url="https://example.test",
    )
    session.add(filing)
    session.flush()

    def tx(asset: Asset, kind: TransactionType, day: date) -> None:
        session.add(
            Transaction(
                filing_id=filing.id,
                asset_id=asset.id,
                owner=OwnerType.SELF,
                transaction_type=kind,
                transaction_date=day,
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
                raw_text="raw",
                parse_confidence=1.0,
                parser_version="test",
            )
        )

    tx(lmt, TransactionType.SALE, date(2026, 7, 15))  # recent, full weight
    tx(gd, TransactionType.PURCHASE, date(2026, 7, 5))  # recent, full weight
    tx(aapl, TransactionType.PURCHASE, date(2026, 7, 10))  # no overlap (no tech cmte)
    session.flush()
    return member


def test_mappings_load_real_files() -> None:
    mappings = load_mappings()
    assert mappings.committee_sectors["SSAS"] == ["defense"]
    assert mappings.ticker_sectors["LMT"] == "defense"
    assert mappings.committee_version == "1.0"
    assert mappings.ticker_version == "1.0"
    assert "@" in mappings.version


def test_label_bands() -> None:
    assert label_for(0.0) is ScoreLabel.LOW
    assert label_for(2.99) is ScoreLabel.LOW
    assert label_for(3.0) is ScoreLabel.MODERATE
    assert label_for(5.99) is ScoreLabel.MODERATE
    assert label_for(6.0) is ScoreLabel.ELEVATED
    assert label_for(9.99) is ScoreLabel.ELEVATED
    assert label_for(10.0) is ScoreLabel.HIGH


def test_golden_scoring_scenario(session: Session) -> None:
    member = _scenario(session)
    rows = score_member(session, member, load_mappings(), DEFAULT_CONFIG, as_of=AS_OF)
    by_component = {row.component: row for row in rows}

    holdings = by_component["committee_sector_holdings_overlap"]
    # 1 sector (defense) x 2.0 + 1 asset (LMT) x 1.0; AAPL excluded (no tech cmte)
    assert holdings.value == 3.0
    assert "defense" in holdings.details
    assert "SSAS" in holdings.details
    assert "Lockheed Martin" in holdings.details
    assert "Apple" not in holdings.details

    trading = by_component["committee_sector_trading_overlap"]
    # base: 1 sector x 2.0 + 2 assets x 1.0 = 4.0; both trades recent -> x1.0
    assert trading.value == 4.0
    assert "General Dynamics" in trading.details

    repeat = by_component["repeat_pattern"]
    assert repeat.value == 0.0  # 2 overlapping trades < threshold 3
    assert "< threshold" in repeat.details

    composite = by_component["composite"]
    # Decomposability: composite equals the exact sum of the components.
    assert composite.value == holdings.value + trading.value + repeat.value
    assert composite.label is ScoreLabel.ELEVATED  # 7.0 >= 6.0
    assert "= 3.0 + 4.0 + 0.0" in composite.details


def test_repeat_pattern_threshold(session: Session) -> None:
    member = _scenario(session)
    filing = session.exec(select(Filing).where(Filing.official_doc_id == "PTRX")).one()
    gd = session.exec(select(Asset).where(Asset.ticker == "GD")).one()
    session.add(
        Transaction(
            filing_id=filing.id,
            asset_id=gd.id,
            owner=OwnerType.SELF,
            transaction_type=TransactionType.PURCHASE,
            transaction_date=date(2026, 7, 20),
            amount_min=Decimal("1001"),
            amount_max=Decimal("15000"),
            raw_text="raw",
            parse_confidence=1.0,
            parser_version="test",
        )
    )
    session.flush()

    rows = score_member(session, member, load_mappings(), DEFAULT_CONFIG, as_of=AS_OF)
    by_component = {row.component: row for row in rows}
    assert by_component["repeat_pattern"].value == DEFAULT_CONFIG.repeat_weight
    assert ">= threshold" in by_component["repeat_pattern"].details
    assert by_component["composite"].value == 3.0 + 4.0 + 2.0
    assert by_component["composite"].label is ScoreLabel.ELEVATED


def test_stale_trades_decay_out(session: Session) -> None:
    member = _scenario(session)
    rows_old = score_member(
        session, member, load_mappings(), DEFAULT_CONFIG, as_of=date(2028, 1, 1)
    )
    trading = {r.component: r for r in rows_old}["committee_sector_trading_overlap"]
    assert trading.value == 0.0  # all trades older than the half window


def test_run_scoring_is_deterministic(session: Session) -> None:
    _scenario(session)
    mappings = load_mappings()
    first = run_scoring(session, mappings, DEFAULT_CONFIG, as_of=AS_OF)
    second = run_scoring(session, mappings, DEFAULT_CONFIG, as_of=AS_OF)
    assert first.id != second.id

    def composites(run_id: int) -> dict[int, float]:
        rows = session.exec(
            select(ScoreComponent).where(
                ScoreComponent.score_run_id == run_id,
                ScoreComponent.component == "composite",
            )
        ).all()
        return {row.member_id: row.value for row in rows}

    assert composites(first.id) == composites(second.id)  # same inputs, same values


def test_notes_crud_round_trip(session: Session) -> None:
    member = _scenario(session)
    filing = session.exec(select(Filing).where(Filing.official_doc_id == "PTRX")).one()

    with pytest.raises(ValueError, match="attach"):
        add_note(session, "no target")
    with pytest.raises(ValueError, match="empty"):
        add_note(session, "   ", member_id=member.id)

    add_note(session, "watch this member", member_id=member.id)
    add_note(session, "check this filing", member_id=member.id, filing_id=filing.id)
    add_note(session, "filing only", filing_id=filing.id)

    assert len(list_notes(session)) == 3
    assert len(list_notes(session, member_id=member.id)) == 2
    assert len(list_notes(session, filing_id=filing.id)) == 2
    both = list_notes(session, member_id=member.id, filing_id=filing.id)
    assert len(both) == 1
    assert both[0].body == "check this filing"
