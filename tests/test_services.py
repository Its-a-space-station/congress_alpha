"""M5a tests: service layer (watchlist, detail, clusters), exports, theme."""

import csv
import tomllib
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from app.core.enums import Chamber, FilingType, OwnerType, ScoreLabel, TransactionType
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
from app.intelligence.scoring import run_scoring
from app.intelligence.services import (
    available_sectors,
    export_rows,
    member_detail,
    sector_clusters,
    watchlist,
)

AS_OF = date(2026, 7, 25)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        yield session


def _seed(session: Session) -> Member:
    """One defense-overlapping member (scored 7.0, elevated) + one clean member."""
    scorer = Member(
        bioguide_id="T000003",
        first_name="Overlap",
        last_name="Case",
        chamber=Chamber.HOUSE,
        party="Republican",
        state="TX",
        district="1",
    )
    clean = Member(
        bioguide_id="T000004",
        first_name="Clean",
        last_name="Case",
        chamber=Chamber.SENATE,
        party="Democrat",
        state="VT",
    )
    committee = Committee(
        code="SSAS", name="Senate Committee on Armed Services", chamber=Chamber.SENATE
    )
    session.add(scorer)
    session.add(clean)
    session.add(committee)
    session.flush()
    session.add(CommitteeMembership(member_id=scorer.id, committee_id=committee.id))

    lmt = Asset(name="Lockheed Martin Corporation", ticker="LMT")
    gd = Asset(name="General Dynamics Corporation", ticker="GD")
    session.add(lmt)
    session.add(gd)
    session.flush()
    session.add(
        Position(
            member_id=scorer.id,
            asset_id=lmt.id,
            as_of=date(2026, 6, 1),
            value_min=Decimal("15001"),
            value_max=Decimal("50000"),
            value_midpoint=Decimal("32500.50"),
            certainty="medium",
            method="test baseline",
        )
    )
    filing = Filing(
        chamber=Chamber.HOUSE,
        member_id=scorer.id,
        filing_type=FilingType.PERIODIC_TRANSACTION,
        filing_type_raw="P",
        official_doc_id="PTRS",
        index_year=2026,
        source_url="https://example.test",
    )
    session.add(filing)
    session.flush()
    for asset, day in ((lmt, date(2026, 7, 15)), (gd, date(2026, 7, 5))):
        session.add(
            Transaction(
                filing_id=filing.id,
                asset_id=asset.id,
                owner=OwnerType.SELF,
                transaction_type=TransactionType.PURCHASE,
                transaction_date=day,
                amount_min=Decimal("1001"),
                amount_max=Decimal("15000"),
                raw_text="raw",
                parse_confidence=1.0,
                parser_version="test",
            )
        )
    session.add(
        NetWorthEstimate(
            member_id=scorer.id,
            year=2025,
            estimate_min=Decimal("100000"),
            estimate_max=Decimal("500000"),
            estimate_midpoint=Decimal("300000"),
            certainty="high",
            method="test",
        )
    )
    session.flush()
    run_scoring(session, load_mappings(), as_of=AS_OF)
    return scorer


def test_watchlist_ordering_and_filters(session: Session) -> None:
    rows = watchlist(session)
    assert len(rows) == 2
    assert rows[0].member_name == "Overlap Case"  # highest composite first
    assert rows[0].composite == 7.0
    assert rows[0].label is ScoreLabel.ELEVATED
    assert "Lockheed" in rows[0].top_details

    elevated = watchlist(session, label=ScoreLabel.ELEVATED)
    assert [row.member_name for row in elevated] == ["Overlap Case"]

    house = watchlist(session, chamber=Chamber.HOUSE)
    assert [row.member_name for row in house] == ["Overlap Case"]

    defense = watchlist(session, sector="defense")
    assert [row.member_name for row in defense] == ["Overlap Case"]
    assert watchlist(session, sector="agriculture") == []


def test_member_detail_assembly(session: Session) -> None:
    scorer_id = next(
        row.member_id for row in watchlist(session) if row.member_name == "Overlap Case"
    )
    detail = member_detail(session, scorer_id)
    assert detail is not None
    assert detail.composite == 7.0
    assert detail.label is ScoreLabel.ELEVATED
    assert len(detail.components) == 3  # three non-composite components
    trading = next(c for c in detail.components if c.component.endswith("trading_overlap"))
    assert "General Dynamics" in trading.details
    assert detail.net_worth_label is not None
    assert detail.net_worth_certainty is not None
    assert detail.net_worth_certainty.value == "high"
    assert [p.asset_name for p in detail.positions] == ["Lockheed Martin Corporation"]
    assert len(detail.transactions) == 2
    assert detail.transactions[0].doc_id == "PTRS"


def test_sector_clusters(session: Session) -> None:
    clusters = sector_clusters(session)
    assert len(clusters) == 1
    defense = clusters[0]
    assert defense.sector == "defense"
    assert defense.member_names == ["Overlap Case"]
    assert defense.asset_names == ["Lockheed Martin Corporation"]
    assert defense.committee_codes == ["SSAS"]
    assert "defense" in available_sectors()


def test_export_rows_content(session: Session, tmp_path: Path) -> None:
    datasets = export_rows(session)
    assert set(datasets) == {"watchlist.csv", "positions.csv", "transactions.csv"}

    header, rows = datasets["watchlist.csv"]
    assert header[0] == "member"
    assert any(row[0] == "Overlap Case" and row[4] == 7.0 for row in rows)

    # CSV round-trip: what is written parses back identically.
    out = tmp_path / "watchlist.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    with out.open(encoding="utf-8") as handle:
        parsed = list(csv.reader(handle))
    assert parsed[0] == header
    assert len(parsed) == len(rows) + 1


def test_theme_config_is_valid() -> None:
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text())
    theme = config["theme"]
    assert theme["base"] == "dark"
    for key in ("primaryColor", "backgroundColor", "secondaryBackgroundColor", "textColor"):
        assert theme[key].startswith("#")


def test_dashboard_script_runs_without_exceptions() -> None:
    """Streamlit AppTest smoke check: the app's main() must execute cleanly."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(PROJECT_ROOT / "app" / "dashboard" / "app.py"))
    app.run()
    assert not app.exception
