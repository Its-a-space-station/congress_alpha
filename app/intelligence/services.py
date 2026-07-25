"""Service layer for the dashboard (M5a).

Typed read models consumed by the Streamlit app and the CSV exporter. No
rendering code here, and no business logic in the dashboard — this module is
the boundary between them. Scores come from the latest ScoreRun; overlap
aggregation reads raw data through the curated mappings (a read model, not
a re-scoring).
"""

import logging
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, select

from app.core.enums import CertaintyLabel, Chamber, ScoreLabel
from app.db.models import (
    Asset,
    Committee,
    CommitteeMembership,
    Filing,
    Member,
    NetWorthEstimate,
    Note,
    Position,
    ScoreComponent,
    ScoreRun,
    Transaction,
)
from app.intelligence.mappings import Mappings, load_mappings
from app.intelligence.notes import list_notes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchlistRow:
    """One ranked watchlist entry."""

    member_id: int
    member_name: str
    chamber: Chamber
    party: str
    state_label: str  # e.g. "GA-12" or "WA"
    composite: float
    label: ScoreLabel | None
    top_component: str
    top_details: str


@dataclass(frozen=True)
class ScoreBreakdown:
    """One component of a member's score, for display."""

    component: str
    value: float
    label: ScoreLabel | None
    details: str


@dataclass(frozen=True)
class PositionRow:
    asset_name: str
    ticker: str | None
    value_label: str
    certainty: CertaintyLabel
    method: str
    as_of: date


@dataclass(frozen=True)
class TransactionRow:
    asset_name: str
    ticker: str | None
    kind: str
    owner: str
    transaction_date: date | None
    amount_label: str
    parse_confidence: float
    doc_id: str | None


@dataclass(frozen=True)
class MemberDetail:
    """Everything the member-detail view renders."""

    member_id: int
    name: str
    chamber: Chamber
    party: str
    state_label: str
    composite: float
    label: ScoreLabel | None
    components: list[ScoreBreakdown]
    net_worth_label: str | None
    net_worth_certainty: CertaintyLabel | None
    positions: list[PositionRow]
    transactions: list[TransactionRow]
    notes: list[Note]


@dataclass(frozen=True)
class SectorCluster:
    """Overlap aggregated for one sector."""

    sector: str
    member_names: list[str]
    asset_names: list[str]
    committee_codes: list[str]


def _money_range(value_min: object, value_max: object, mid: object) -> str:
    """Compact display label for a ranged value."""
    if value_min is None and value_max is None:
        return "disclosed None"
    if value_min is None or value_max is None:
        return f"{value_min or value_max} (open range)"
    return f"{value_min} – {value_max} (mid {mid})"


def _latest_run(session: Session) -> ScoreRun | None:
    return session.exec(select(ScoreRun).order_by(ScoreRun.id.desc())).first()  # type: ignore[union-attr]


def _state_label(member: Member) -> str:
    return f"{member.state}-{member.district}" if member.district else member.state


def watchlist(
    session: Session,
    *,
    chamber: Chamber | None = None,
    party: str | None = None,
    label: ScoreLabel | None = None,
    sector: str | None = None,
    limit: int = 200,
) -> list[WatchlistRow]:
    """Ranked watchlist rows from the latest score run, newest first."""
    run = _latest_run(session)
    if run is None:
        return []
    rows = session.exec(
        select(ScoreComponent, Member)
        .join(Member, ScoreComponent.member_id == Member.id)  # type: ignore[arg-type]
        .where(
            ScoreComponent.score_run_id == run.id,
            ScoreComponent.component == "composite",
        )
    ).all()

    entries: list[WatchlistRow] = []
    for composite_row, member in rows:
        if chamber is not None and member.chamber is not chamber:
            continue
        if party is not None and member.party != party:
            continue
        if label is not None and composite_row.label is not label:
            continue
        components = session.exec(
            select(ScoreComponent).where(
                ScoreComponent.score_run_id == run.id,
                ScoreComponent.member_id == member.id,
                ScoreComponent.component != "composite",
            )
        ).all()
        if sector is not None and not any(
            c.details.startswith(f"{sector} via") for c in components if c.details
        ):
            continue
        best = max(components, key=lambda c: c.value, default=None)
        entries.append(
            WatchlistRow(
                member_id=member.id,  # type: ignore[arg-type]
                member_name=f"{member.first_name} {member.last_name}",
                chamber=member.chamber,
                party=member.party,
                state_label=_state_label(member),
                composite=composite_row.value,
                label=composite_row.label,
                top_component=best.component if best else "",
                top_details=(best.details or "") if best else "",
            )
        )
    entries.sort(key=lambda entry: entry.composite, reverse=True)
    return entries[:limit]


def member_detail(session: Session, member_id: int) -> MemberDetail | None:
    """Assemble everything the member-detail view renders."""
    member = session.get(Member, member_id)
    if member is None:
        return None
    run = _latest_run(session)
    composite_value = 0.0
    composite_label: ScoreLabel | None = None
    components: list[ScoreBreakdown] = []
    if run is not None:
        rows = session.exec(
            select(ScoreComponent).where(
                ScoreComponent.score_run_id == run.id,
                ScoreComponent.member_id == member_id,
            )
        ).all()
        for row in rows:
            if row.component == "composite":
                composite_value = row.value
                composite_label = row.label
            else:
                components.append(
                    ScoreBreakdown(
                        component=row.component,
                        value=row.value,
                        label=row.label,
                        details=row.details or "",
                    )
                )

    net_worth = session.exec(
        select(NetWorthEstimate)
        .where(NetWorthEstimate.member_id == member_id)
        .order_by(NetWorthEstimate.year.desc())  # type: ignore[attr-defined]
    ).first()
    net_worth_label = (
        _money_range(
            net_worth.estimate_min, net_worth.estimate_max, net_worth.estimate_midpoint
        )
        if net_worth
        else None
    )

    positions = session.exec(
        select(Position, Asset)
        .join(Asset, Position.asset_id == Asset.id)  # type: ignore[arg-type]
        .where(Position.member_id == member_id)
        .order_by(Position.value_midpoint.desc())  # type: ignore[union-attr]
    ).all()
    position_rows = [
        PositionRow(
            asset_name=asset.name,
            ticker=asset.ticker,
            value_label=_money_range(p.value_min, p.value_max, p.value_midpoint),
            certainty=p.certainty,
            method=p.method,
            as_of=p.as_of,
        )
        for p, asset in positions
    ]

    transactions = session.exec(
        select(Transaction, Asset, Filing)
        .join(Asset, Transaction.asset_id == Asset.id)  # type: ignore[arg-type]
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .where(Filing.member_id == member_id)
        .order_by(Transaction.transaction_date.desc())  # type: ignore[union-attr]
        .limit(25)
    ).all()
    transaction_rows = [
        TransactionRow(
            asset_name=asset.name,
            ticker=asset.ticker,
            kind=tx.transaction_type.value,
            owner=tx.owner.value,
            transaction_date=tx.transaction_date,
            amount_label=_money_range(tx.amount_min, tx.amount_max, tx.amount_midpoint),
            parse_confidence=tx.parse_confidence,
            doc_id=filing.official_doc_id,
        )
        for tx, asset, filing in transactions
    ]

    return MemberDetail(
        member_id=member_id,
        name=f"{member.first_name} {member.last_name}",
        chamber=member.chamber,
        party=member.party,
        state_label=_state_label(member),
        composite=composite_value,
        label=composite_label,
        components=components,
        net_worth_label=net_worth_label,
        net_worth_certainty=net_worth.certainty if net_worth else None,
        positions=position_rows,
        transactions=transaction_rows,
        notes=list_notes(session, member_id=member_id),
    )


def sector_clusters(session: Session, mappings: Mappings | None = None) -> list[SectorCluster]:
    """Aggregate committee/sector overlap across members (read model)."""
    mappings = mappings or load_mappings()
    clusters: dict[str, dict[str, set]] = {}

    memberships = session.exec(
        select(CommitteeMembership, Committee, Member)
        .join(Committee, onclause=CommitteeMembership.committee_id == Committee.id)  # type: ignore[arg-type]
        .join(Member, onclause=CommitteeMembership.member_id == Member.id)  # type: ignore[arg-type]
    ).all()

    member_sectors: dict[int, set[str]] = {}
    committee_sectors: dict[str, set[str]] = {}
    for _membership, committee, member in memberships:
        for sector in mappings.committee_sectors.get(committee.code, []):
            member_sectors.setdefault(member.id, set()).add(sector)  # type: ignore[arg-type]
            committee_sectors.setdefault(sector, set()).add(committee.code)
            clusters.setdefault(sector, {"members": set(), "assets": set()})
            clusters[sector]["members"].add(f"{member.first_name} {member.last_name}")

    for member_id, sectors in member_sectors.items():
        positions = session.exec(
            select(Position, Asset)
            .join(Asset, Position.asset_id == Asset.id)  # type: ignore[arg-type]
            .where(Position.member_id == member_id)
        ).all()
        for _position, asset in positions:
            if asset.ticker is None:
                continue
            asset_sector = mappings.ticker_sectors.get(asset.ticker.upper())
            if asset_sector and asset_sector in sectors:
                clusters.setdefault(asset_sector, {"members": set(), "assets": set()})
                clusters[asset_sector]["assets"].add(asset.name)

    return [
        SectorCluster(
            sector=sector,
            member_names=sorted(data["members"]),
            asset_names=sorted(data["assets"]),
            committee_codes=sorted(committee_sectors.get(sector, set())),
        )
        for sector, data in sorted(clusters.items())
        if data["assets"]  # only sectors with an actual overlap
    ]


def available_sectors(mappings: Mappings | None = None) -> list[str]:
    """Sectors available as watchlist filters (from the curated maps)."""
    mappings = mappings or load_mappings()
    return sorted({s for sectors in mappings.committee_sectors.values() for s in sectors})


def export_rows(session: Session) -> dict[str, tuple[list[str], list[tuple]]]:
    """CSV export datasets: filename -> (header, rows)."""
    watchlist_rows = [
        (
            row.member_name,
            row.chamber.value,
            row.party,
            row.state_label,
            row.composite,
            row.label.value if row.label else "",
            row.top_component,
            row.top_details,
        )
        for row in watchlist(session, limit=10000)
    ]
    positions = session.exec(
        select(Position, Asset, Member)
        .join(Asset, Position.asset_id == Asset.id)  # type: ignore[arg-type]
        .join(Member, Position.member_id == Member.id)  # type: ignore[arg-type]
        .order_by(Member.last_name)
    ).all()
    position_rows = [
        (
            f"{m.first_name} {m.last_name}",
            a.name,
            a.ticker or "",
            p.value_min,
            p.value_max,
            p.value_midpoint,
            p.certainty.value,
            p.method,
            p.as_of.isoformat(),
        )
        for p, a, m in positions
    ]
    transactions = session.exec(
        select(Transaction, Asset, Filing, Member)
        .join(Asset, Transaction.asset_id == Asset.id)  # type: ignore[arg-type]
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .join(Member, Filing.member_id == Member.id)  # type: ignore[arg-type]
        .order_by(Transaction.transaction_date)  # type: ignore[arg-type]
    ).all()
    transaction_rows = [
        (
            f"{m.first_name} {m.last_name}",
            f.official_doc_id or "",
            a.name,
            a.ticker or "",
            tx.transaction_type.value,
            tx.owner.value,
            tx.transaction_date.isoformat() if tx.transaction_date else "",
            tx.amount_min,
            tx.amount_max,
            tx.amount_midpoint,
            tx.parse_confidence,
        )
        for tx, a, f, m in transactions
    ]
    return {
        "watchlist.csv": (
            ["member", "chamber", "party", "state", "composite", "label",
             "top_component", "top_details"],
            watchlist_rows,
        ),
        "positions.csv": (
            ["member", "asset", "ticker", "value_min", "value_max",
             "value_midpoint", "certainty", "method", "as_of"],
            position_rows,
        ),
        "transactions.csv": (
            ["member", "doc_id", "asset", "ticker", "type", "owner", "date",
             "amount_min", "amount_max", "amount_midpoint", "parse_confidence"],
            transaction_rows,
        ),
    }
