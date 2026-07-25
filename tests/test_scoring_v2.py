"""M6 tests: relative_size, track_record, Tiingo parsing, .env, v2 decomposability."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from app.core.enums import Chamber, FilingType, OwnerType, TransactionType
from app.db.models import (
    Asset,
    Committee,
    CommitteeMembership,
    Filing,
    Member,
    NetWorthEstimate,
    Position,
    Transaction,
)
from app.db.session import get_engine
from app.intelligence.mappings import load_mappings
from app.intelligence.scoring import (
    DEFAULT_CONFIG,
    _relative_size_component,
    _track_record_component,
    score_member,
)
from app.intelligence.tiingo import _parse_daily

AS_OF = date(2026, 7, 25)
FIXTURE = Path(__file__).parent / "fixtures" / "tiingo_spy_sample.json"


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _member(session: Session, bioguide: str = "T000006") -> Member:
    member = Member(
        bioguide_id=bioguide, first_name="Vee", last_name="Two",
        chamber=Chamber.HOUSE, party="Independent", state="CA", district="1",
    )
    session.add(member)
    session.flush()
    return member


def _purchase(session: Session, member: Member, amount: str, day: date, filing_day: date) -> None:
    asset = Asset(name="Test Corp", ticker="TEST")
    filing = Filing(
        chamber=Chamber.HOUSE, member_id=member.id,
        filing_type=FilingType.PERIODIC_TRANSACTION, filing_type_raw="P",
        official_doc_id=f"P{day.isoformat()}", index_year=2026,
        filing_date=filing_day, source_url="https://example.test",
    )
    session.add(asset)
    session.add(filing)
    session.flush()
    session.add(
        Transaction(
            filing_id=filing.id, asset_id=asset.id, owner=OwnerType.SELF,
            transaction_type=TransactionType.PURCHASE, transaction_date=day,
            amount_min=Decimal(amount), amount_max=Decimal(amount),
            raw_text="raw", parse_confidence=1.0, parser_version="test",
        )
    )
    session.flush()


def test_tiingo_fixture_parses() -> None:
    series = _parse_daily(FIXTURE.read_bytes())
    assert len(series) == 5
    assert series[date(2026, 7, 24)] == pytest.approx(738.93)


def test_secret_env_beats_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as config

    (tmp_path / ".env").write_text("TIINGO_API_KEY=file-key\n")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("TIINGO_API_KEY", "env-key")
    assert config.get_secret("TIINGO_API_KEY") == "env-key"
    monkeypatch.delenv("TIINGO_API_KEY")
    assert config.get_secret("TIINGO_API_KEY") == "file-key"
    assert config.get_secret("NO_SUCH_KEY") is None


def test_relative_size_math(session: Session) -> None:
    member = _member(session)
    session.add(
        NetWorthEstimate(
            member_id=member.id, year=2025,
            estimate_min=Decimal("1000000"), estimate_max=Decimal("2000000"),
            estimate_midpoint=Decimal("1500000"), certainty="high", method="test",
        )
    )
    session.flush()

    # ratio = 100k / 2M = 5% -> clamped at 2% cap -> full weight 2.0
    _purchase(session, member, "100000", date(2026, 6, 1), date(2026, 6, 15))
    value, details = _relative_size_component(session, member.id, DEFAULT_CONFIG, AS_OF)
    assert value == 2.0
    assert "$100,000" in details

    # outside the window -> not counted
    old_only = _member(session, "T000007")
    _purchase(session, old_only, "100000", date(2024, 1, 1), date(2024, 1, 15))
    value_old, details_old = _relative_size_component(session, old_only.id, DEFAULT_CONFIG, AS_OF)
    assert value_old == 0.0
    assert details_old in ("no purchases in window", "no basis (no net-worth estimate)")


def test_relative_size_no_basis(session: Session) -> None:
    member = _member(session)
    value, details = _relative_size_component(session, member.id, DEFAULT_CONFIG, AS_OF)
    assert value == 0.0
    assert details == "no basis (no net-worth estimate)"


def test_track_record_gating_and_value(session: Session) -> None:
    member = _member(session)
    # 4 signals: below the 5 minimum -> gated to zero.
    signal_days = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 4)]
    for day in signal_days:
        _purchase(session, member, "15000", day, day)

    # Series with closes at every t0 (100) and every t0+30 (110): +10% each.
    stock_series = {day: 100.0 for day in signal_days}
    stock_series[date(2026, 4, 10)] = 100.0
    for day in (
        date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3),
        date(2026, 5, 4), date(2026, 5, 10),
    ):
        stock_series[day] = 110.0
    spy_flat = dict.fromkeys(stock_series, 100.0)

    def provider(ticker: str) -> dict[date, float]:
        return spy_flat if ticker == "SPY" else stock_series

    value, details = _track_record_component(
        session, member.id, DEFAULT_CONFIG, AS_OF, provider
    )
    assert value == 0.0
    assert "insufficient history (n=4 < 5)" in details

    # 5th signal: gate opens; every buy beats flat SPY by +10% -> clamp at +10%.
    _purchase(session, member, "15000", date(2026, 4, 10), date(2026, 4, 10))
    value2, details2 = _track_record_component(
        session, member.id, DEFAULT_CONFIG, AS_OF, provider
    )
    assert value2 == 2.0  # weight 20 x clamped +0.10
    assert details2.startswith("n=5, mean 30d excess vs SPY +10.0%")
    # float excess lands a hair over +10% -> the clamp note proves the bound works
    assert "clamped to +10.0%" in details2


def test_track_record_point_in_time(session: Session) -> None:
    member = _member(session)
    # All filings too recent for a completed 30d window before AS_OF.
    for i in range(6):
        day = date(2026, 7, 10 + i)
        _purchase(session, member, "15000", day, day)
    provider = lambda _ticker: {date(2026, 7, 10): 100.0, date(2026, 8, 20): 200.0}  # noqa: E731
    value, details = _track_record_component(
        session, member.id, DEFAULT_CONFIG, AS_OF, provider
    )
    assert value == 0.0
    assert "insufficient history (n=0 < 5)" in details


def test_five_component_decomposability(session: Session) -> None:
    member = _member(session)
    committee = Committee(
        code="SSAS", name="Senate Committee on Armed Services", chamber=Chamber.SENATE
    )
    lmt = Asset(name="Lockheed Martin Corporation", ticker="LMT")
    session.add(committee)
    session.add(lmt)
    session.flush()
    session.add(CommitteeMembership(member_id=member.id, committee_id=committee.id))

    session.add(
        Position(
            member_id=member.id, asset_id=lmt.id, as_of=date(2026, 6, 1),
            value_min=Decimal("15001"), value_max=Decimal("50000"),
            certainty="medium", method="test",
        )
    )
    session.add(
        NetWorthEstimate(
            member_id=member.id, year=2025,
            estimate_min=Decimal("1000000"), estimate_max=Decimal("2000000"),
            estimate_midpoint=Decimal("1500000"), certainty="high", method="test",
        )
    )
    session.flush()
    _purchase(session, member, "100000", date(2026, 6, 1), date(2026, 6, 15))

    rows = score_member(session, member, load_mappings(), DEFAULT_CONFIG, as_of=AS_OF)
    by_component = {row.component: row for row in rows}
    assert set(by_component) == {
        "committee_sector_holdings_overlap",
        "committee_sector_trading_overlap",
        "repeat_pattern",
        "relative_size",
        "track_record",
        "composite",
    }
    composite = by_component["composite"]
    total = sum(
        row.value for name, row in by_component.items() if name != "composite"
    )
    assert composite.value == pytest.approx(total)
    assert by_component["relative_size"].value == 2.0
    assert by_component["track_record"].details == "no price provider configured"
