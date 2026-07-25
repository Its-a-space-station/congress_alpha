"""M5b tests: validation math (exact, synthetic), guards, refresh runner."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from app.core.enums import Chamber, FilingType, OwnerType, TransactionType
from app.db.models import Asset, Committee, CommitteeMembership, Filing, Member, Transaction
from app.db.session import get_engine
from app.intelligence.prices import close_on_or_after, close_on_or_before
from app.intelligence.validation import (
    ValidationReport,
    _metrics,
    collect_signals,
    forward_return,
    run_validation,
    wilson_interval,
    write_report,
)
from app.jobs.refresh import run_refresh


def test_forward_return_exact_math() -> None:
    series = {
        date(2026, 1, 1): 50.0,  # before t0 — must never be used as entry
        date(2026, 1, 5): 100.0,
        date(2026, 2, 4): 110.0,
        date(2026, 4, 5): 121.0,
    }
    t0 = date(2026, 1, 5)
    assert forward_return(series, t0, 30) == pytest.approx(0.10)
    assert forward_return(series, t0, 90) == pytest.approx(0.21)
    # Entry is the first close ON or AFTER t0, never the earlier one.
    assert close_on_or_after(series, t0) == (date(2026, 1, 5), 100.0)
    assert close_on_or_before(series, date(2026, 1, 4)) == (date(2026, 1, 1), 50.0)
    # No closes after t0 -> no return (signal dropped, not faked).
    assert forward_return(series, date(2026, 12, 31), 30) is None


def test_wilson_interval_known_values() -> None:
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_metrics_thin_sample_flag() -> None:
    assert _metrics(30, [0.01, 0.02, 0.03]).thin_sample is True
    assert _metrics(30, [0.01] * 10).thin_sample is False
    empty = _metrics(30, [])
    assert (empty.signals, empty.mean_excess_return, empty.thin_sample) == (0, None, True)


@pytest.fixture(name="session")
def session_fixture():
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        member = Member(
            bioguide_id="T000005", first_name="Val", last_name="Id",
            chamber=Chamber.HOUSE, party="Independent", state="CA", district="1",
        )
        committee = Committee(
            code="SSAS", name="Senate Committee on Armed Services", chamber=Chamber.SENATE
        )
        session.add(member)
        session.add(committee)
        session.flush()
        session.add(CommitteeMembership(member_id=member.id, committee_id=committee.id))

        lmt = Asset(name="Lockheed Martin Corporation", ticker="LMT")
        aapl = Asset(name="Apple Inc. - Common Stock", ticker="AAPL")
        noticker = Asset(name="Private Fund LP", ticker=None)
        for asset in (lmt, aapl, noticker):
            session.add(asset)
        filing = Filing(
            chamber=Chamber.HOUSE, member_id=member.id,
            filing_type=FilingType.PERIODIC_TRANSACTION, filing_type_raw="P",
            official_doc_id="PTRV", index_year=2025, filing_date=date(2026, 1, 5),
            source_url="https://example.test",
        )
        filing_no_date = Filing(
            chamber=Chamber.HOUSE, member_id=member.id,
            filing_type=FilingType.PERIODIC_TRANSACTION, filing_type_raw="P",
            official_doc_id="PTRW", index_year=2025, filing_date=None,
            source_url="https://example.test",
        )
        session.add(filing)
        session.add(filing_no_date)
        session.flush()

        def tx(asset, kind, f):
            session.add(
                Transaction(
                    filing_id=f.id, asset_id=asset.id, owner=OwnerType.SELF,
                    transaction_type=kind, transaction_date=date(2025, 12, 15),
                    amount_min=Decimal("1001"), amount_max=Decimal("15000"),
                    raw_text="raw", parse_confidence=1.0, parser_version="test",
                )
            )

        tx(lmt, TransactionType.PURCHASE, filing)  # overlap (defense via SSAS)
        tx(aapl, TransactionType.PURCHASE, filing)  # no overlap (no tech cmte)
        tx(aapl, TransactionType.SALE, filing)  # sales are not signals
        tx(noticker, TransactionType.PURCHASE, filing)  # skipped: no ticker
        tx(aapl, TransactionType.PURCHASE, filing_no_date)  # skipped: no t0
        session.flush()
        yield session


def test_collect_signals_filters_and_overlap(session: Session) -> None:
    signals, skipped = collect_signals(session)
    assert len(signals) == 2
    assert skipped == 2  # no-ticker + no-filing-date
    by_ticker = {s.ticker: s for s in signals}
    assert by_ticker["LMT"].sector_overlap is True
    assert by_ticker["AAPL"].sector_overlap is False
    assert all(s.t0 == date(2026, 1, 5) for s in signals)


def test_run_validation_synthetic_provider(session: Session) -> None:
    lmt_series = {
        date(2026, 1, 5): 100.0,
        date(2026, 2, 4): 120.0,  # +20% at 30d
        date(2026, 4, 5): 130.0,  # +30% at 90d
    }
    aapl_series = {
        date(2026, 1, 5): 100.0,
        date(2026, 2, 4): 90.0,  # -10% at 30d
        date(2026, 4, 5): 80.0,  # -20% at 90d
    }
    spy_series = {
        date(2026, 1, 5): 100.0,
        date(2026, 2, 4): 100.0,  # flat benchmark
        date(2026, 4, 5): 100.0,
    }
    series_by_ticker = {"LMT": lmt_series, "AAPL": aapl_series, "SPY": spy_series}

    report = run_validation(
        session, lambda ticker: series_by_ticker.get(ticker, {}), as_of=date(2026, 7, 1)
    )

    overall_30 = next(m for m in report.horizons if m.horizon_days == 30)
    assert overall_30.signals == 2
    assert overall_30.mean_excess_return == pytest.approx((0.20 - 0.10) / 2)
    assert overall_30.hit_rate == 0.5
    assert overall_30.thin_sample is True

    overlap_30 = next(m for m in report.cohorts["overlap"] if m.horizon_days == 30)
    assert overlap_30.signals == 1
    assert overlap_30.mean_excess_return == pytest.approx(0.20)
    assert "PTR purchase signal validation" in report.caveats[0]


def test_write_report_files(session: Session, tmp_path: Path) -> None:
    report = ValidationReport(
        generated_at="2026-07-25T00:00:00+00:00",
        data_as_of="2026-07-25",
        scope="PTR purchase signal validation (filing-date t0, excess vs SPY)",
        caveats=["test caveat"],
        horizons=[_metrics(30, [0.05, -0.02])],
    )
    import json as _json

    json_path, md_path = write_report(report, tmp_path)
    parsed = _json.loads(json_path.read_text())
    assert parsed["scope"].startswith("PTR purchase signal validation")
    assert parsed["horizons"][0]["signals"] == 2
    markdown = md_path.read_text()
    assert "PTR purchase signal validation" in markdown
    assert "test caveat" in markdown


def test_refresh_runner_stops_loudly() -> None:
    calls: list[str] = []

    def ok(name: str):
        return lambda: calls.append(name)

    def boom() -> None:
        raise ValueError("kaput")

    completed = run_refresh([("a", ok("a")), ("b", ok("b"))])
    assert completed == ["a", "b"]

    with pytest.raises(RuntimeError, match="failed at stage 'parse'"):
        run_refresh([("ingest", ok("ingest")), ("parse", boom), ("score", ok("score"))])
    assert calls == ["a", "b", "ingest"]  # score never ran
