"""M8 tests: alpha-decay + signal-persistence analysis (synthetic, no network).

Known-answer price grids: SPY is flat so excess return equals the stock's own
return. TEST rises +10% in the invisible window (trade -> filing), +5% in the
first 30d copy window, +10% over 90d, and +18% over 90d from the trade date.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from app.core.enums import Chamber, FilingType, OwnerType, TransactionType
from app.db.models import Asset, Filing, Member, Transaction
from app.db.session import get_engine
from app.intelligence.analysis import (
    bucket_for_amount,
    bucket_for_lag,
    collect_trade_signals,
    run_analysis,
    spearman,
    write_analysis_report,
)

TRADE = date(2024, 2, 1)
FILING = date(2024, 3, 2)  # lag = 30 days

# Sparse grid; close_on_or_after/before resolve to these points.
TEST_SERIES = {
    date(2024, 2, 1): 100.0,   # trade date
    date(2024, 3, 2): 110.0,   # filing date: +10% invisible window
    date(2024, 4, 1): 115.5,   # filing +30d: +5% copy window
    date(2024, 5, 1): 118.0,   # trade +90d: +18% from entry
    date(2024, 5, 31): 121.0,  # filing +90d: +10% copy window
}
SPY_SERIES = {day: 400.0 for day in TEST_SERIES}  # flat benchmark


def _provider(series_by_ticker: dict[str, dict[date, float]]):
    def provider(ticker: str) -> dict[date, float]:
        return series_by_ticker.get(ticker, {})

    return provider


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _member(session: Session, bioguide: str, last: str) -> Member:
    member = Member(
        bioguide_id=bioguide, first_name="Test", last_name=last,
        chamber=Chamber.HOUSE, party="Independent", state="CA", district="1",
    )
    session.add(member)
    session.flush()
    return member


def _purchase(
    session: Session,
    member: Member,
    *,
    trade_day: date,
    filing_day: date,
    amount_min: str = "15001",
    amount_max: str = "50000",
    ticker: str = "TEST",
) -> None:
    asset = Asset(name=f"{ticker} Corp", ticker=ticker)
    filing = Filing(
        chamber=Chamber.HOUSE, member_id=member.id,
        filing_type=FilingType.PERIODIC_TRANSACTION, filing_type_raw="P",
        official_doc_id=f"P{ticker}{trade_day.isoformat()}{amount_min}",
        index_year=trade_day.year, filing_date=filing_day,
        source_url="https://example.test",
    )
    session.add(asset)
    session.add(filing)
    session.flush()
    session.add(
        Transaction(
            filing_id=filing.id, asset_id=asset.id, owner=OwnerType.SELF,
            transaction_type=TransactionType.PURCHASE, transaction_date=trade_day,
            amount_min=Decimal(amount_min), amount_max=Decimal(amount_max),
            raw_text="raw", parse_confidence=1.0, parser_version="test",
        )
    )
    session.flush()


def _report(session: Session, series: dict[str, dict[date, float]] | None = None):
    grid = series if series is not None else {"TEST": TEST_SERIES, "SPY": SPY_SERIES}
    return run_analysis(session, _provider(grid), as_of=date(2024, 6, 30))


def test_bucket_helpers() -> None:
    assert bucket_for_lag(0) == "0-9"
    assert bucket_for_lag(9) == "0-9"
    assert bucket_for_lag(10) == "10-19"
    assert bucket_for_lag(30) == "30-44"
    assert bucket_for_lag(45) == "45+"
    assert bucket_for_amount(Decimal("1000")) == "<=15k"
    assert bucket_for_amount(Decimal("15001")) == "15k-50k"
    assert bucket_for_amount(Decimal("50001")) == "50k-100k"
    assert bucket_for_amount(Decimal("100001")) == "100k-250k"
    assert bucket_for_amount(Decimal("250001")) == "250k-500k"
    assert bucket_for_amount(Decimal("500001")) == "500k+"
    assert bucket_for_amount(None) == "unknown"


def test_collect_trade_signals_requires_dates_and_ticker(session: Session) -> None:
    member = _member(session, "T000001", "One")
    _purchase(session, member, trade_day=TRADE, filing_day=FILING)
    # No ticker on the asset: must be skipped.
    asset = Asset(name="Mystery fund", ticker=None)
    filing = Filing(
        chamber=Chamber.HOUSE, member_id=member.id,
        filing_type=FilingType.PERIODIC_TRANSACTION, filing_type_raw="P",
        official_doc_id="P-noticker", index_year=2024, filing_date=FILING,
        source_url="https://example.test",
    )
    session.add(asset)
    session.add(filing)
    session.flush()
    session.add(
        Transaction(
            filing_id=filing.id, asset_id=asset.id, owner=OwnerType.SELF,
            transaction_type=TransactionType.PURCHASE, transaction_date=TRADE,
            amount_min=Decimal("1001"), amount_max=Decimal("15000"),
            raw_text="raw", parse_confidence=1.0, parser_version="test",
        )
    )
    session.flush()

    signals, skipped = collect_trade_signals(session)
    assert len(signals) == 1
    assert skipped == 1
    signal = signals[0]
    assert signal.ticker == "TEST"
    assert signal.trade_date == TRADE
    assert signal.filing_date == FILING


def test_lag_decay_windows(session: Session) -> None:
    member = _member(session, "T000002", "Two")
    _purchase(session, member, trade_day=TRADE, filing_day=FILING)
    report = _report(session)

    assert report.lag_decay["overall"]["signals"] == 1
    assert report.lag_decay["overall"]["mean_invisible_excess"] == pytest.approx(0.10)
    assert report.lag_decay["overall"]["median_lag_days"] == 30.0

    bucket = report.lag_decay["buckets"]["30-44"]
    assert bucket["signals"] == 1
    assert bucket["mean_invisible_excess"] == pytest.approx(0.10)
    assert bucket["mean_copy_30d_excess"] == pytest.approx(0.05)


def test_entry_vs_copy_t0(session: Session) -> None:
    member = _member(session, "T000003", "Three")
    _purchase(session, member, trade_day=TRADE, filing_day=FILING)
    report = _report(session)

    entry = {m.horizon_days: m for m in report.entry_vs_copy["entry_t0_trade_date"]}
    copy = {m.horizon_days: m for m in report.entry_vs_copy["copy_t0_filing_date"]}
    assert entry[30].mean_excess_return == pytest.approx(0.10)
    assert entry[90].mean_excess_return == pytest.approx(0.18)
    assert copy[30].mean_excess_return == pytest.approx(0.05)
    assert copy[90].mean_excess_return == pytest.approx(0.10)


def test_negative_lag_excluded(session: Session) -> None:
    member = _member(session, "T000004", "Four")
    _purchase(session, member, trade_day=TRADE, filing_day=FILING)
    # Amendment-style oddity: filing date BEFORE the trade date.
    _purchase(
        session, member, trade_day=FILING, filing_day=TRADE,
        amount_min="1001", amount_max="15000",
    )
    report = _report(session)
    assert report.lag_decay["overall"]["signals"] == 1
    assert report.lag_decay["excluded_negative_lag"] == 1


def test_size_buckets(session: Session) -> None:
    member = _member(session, "T000005", "Five")
    _purchase(session, member, trade_day=TRADE, filing_day=FILING)
    _purchase(
        session, member, trade_day=TRADE, filing_day=FILING,
        amount_min="500001", amount_max="1000000",
    )
    report = _report(session)
    assert report.size_buckets["15k-50k"]["signals"] == 1
    assert report.size_buckets["500k+"]["signals"] == 1
    assert report.size_buckets["15k-50k"]["mean_30d_excess"] == pytest.approx(0.10)


def test_spearman_known_answers() -> None:
    assert spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)
    assert spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)
    assert spearman([1.0], [2.0]) is None
    assert spearman([1.0, 1.0], [2.0, 3.0]) is None  # zero variance


def test_persistence_split_and_retention(session: Session) -> None:
    """Two members with 10 trades per period each; ranks flip perfectly."""
    series: dict[str, dict[date, float]] = {"SPY": {}}
    spy_grid: dict[date, float] = {}

    def add_series(ticker: str, p1_return: float, p2_return: float) -> None:
        # P1: trade 2020-01-02 -> +30d exit 2020-02-01; P2: 2021-01-04 -> 2021-02-03.
        series[ticker] = {
            date(2020, 1, 2): 100.0,
            date(2020, 2, 1): 100.0 * (1 + p1_return),
            date(2021, 1, 4): 100.0,
            date(2021, 2, 3): 100.0 * (1 + p2_return),
        }
        for day in series[ticker]:
            spy_grid.setdefault(day, 400.0)

    # Alpha keeps improving (+5% then +8%); Beta decays (+8% then +5%).
    add_series("AAA", 0.05, 0.08)
    add_series("BBB", 0.08, 0.05)
    series["SPY"] = spy_grid

    alpha = _member(session, "T000006", "Alpha")
    beta = _member(session, "T000007", "Beta")
    for _i in range(10):
        _purchase(
            session, alpha, trade_day=date(2020, 1, 2), filing_day=date(2020, 2, 1),
            amount_min="15001", amount_max="50000", ticker="AAA",
        )
        _purchase(
            session, alpha, trade_day=date(2021, 1, 4), filing_day=date(2021, 2, 3),
            amount_min="15001", amount_max="50000", ticker="AAA",
        )
        _purchase(
            session, beta, trade_day=date(2020, 1, 2), filing_day=date(2020, 2, 1),
            amount_min="15001", amount_max="50000", ticker="BBB",
        )
        _purchase(
            session, beta, trade_day=date(2021, 1, 4), filing_day=date(2021, 2, 3),
            amount_min="15001", amount_max="50000", ticker="BBB",
        )

    report = run_analysis(session, _provider(series), as_of=date(2024, 6, 30))
    persistence = report.persistence
    assert persistence["members"] == 2
    # Perfect rank reversal between periods -> Spearman -1.
    assert persistence["spearman"] == pytest.approx(-1.0)
    rows = {row["member"]: row for row in persistence["table"]}
    assert rows["Test Alpha"]["p1_mean_30d_excess"] == pytest.approx(0.05)
    assert rows["Test Alpha"]["p2_mean_30d_excess"] == pytest.approx(0.08)
    assert rows["Test Beta"]["p1_mean_30d_excess"] == pytest.approx(0.08)
    assert rows["Test Beta"]["p2_mean_30d_excess"] == pytest.approx(0.05)


def test_write_analysis_report(session: Session, tmp_path: Path) -> None:
    member = _member(session, "T000008", "Eight")
    _purchase(session, member, trade_day=TRADE, filing_day=FILING)
    report = _report(session)
    json_path, md_path = write_analysis_report(report, tmp_path)
    assert json_path.name == "alpha_decay_report.json"
    assert md_path.name == "alpha_decay_report.md"
    import json

    payload = json.loads(json_path.read_text())
    for key in ("lag_decay", "entry_vs_copy", "persistence", "size_buckets", "caveats"):
        assert key in payload
    assert "30-44" in md_path.read_text()
