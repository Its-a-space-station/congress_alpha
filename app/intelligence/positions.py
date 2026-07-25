"""Position reconstruction (M3). Conservative, documented, deterministic.

Method (stated for auditability; see CLAUDE.md estimation rules):

1. BASELINE — the member's latest parsed annual FD. Each asset group in its
   holdings is an estimated open position with the holding's disclosed value
   range. Certainty MEDIUM: presence is directly disclosed, but the value is
   a range and ages from the filing date.
2. PTR OVERLAY — the member's parsed PTR transactions dated after the
   baseline date, grouped by asset key, applied chronologically:
   - PURCHASE of a group absent from the baseline -> new estimated position
     (cumulative purchase range), certainty LOW (no baseline corroboration).
   - PURCHASE of a baseline group -> corroborates the position; range kept,
     method notes post-baseline buying.
   - SALE of a baseline group -> the position may be reduced or closed;
     certainty drops to LOW, method notes post-baseline selling. ("S
     (partial)" rows always carry this note.)
   - SALE of a group never seen buying nor in baseline -> unobservable;
     recorded in stats only.
3. Ranged arithmetic only: cumulative sums keep min and max independently.
   No share counts, no midpoint arithmetic presented as precise — midpoints
   are computed only for display via normalize.midpoint.

Derived rows are rebuilt deterministically on every run (see cli.reconstruct).
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from app.core.enums import CertaintyLabel, TransactionType
from app.db.models import Asset, Filing, Holding, Position, Transaction
from app.intelligence.checkpoint import MemberDataReport
from app.intelligence.normalize import asset_group_key, midpoint

logger = logging.getLogger(__name__)

METHOD_BASELINE = "annual-fd baseline holding (value range as disclosed)"
METHOD_PTR_NEW = "ptr purchase(s) after baseline; no annual-fd baseline"
METHOD_CORROBORATED = "annual-fd baseline + post-baseline ptr purchases"
METHOD_MAYBE_REDUCED = "annual-fd baseline; post-baseline ptr sale(s) — may be reduced/closed"


@dataclass
class _GroupState:
    """Accumulator for one asset group during reconstruction."""

    asset_id: int | None
    value_min: Decimal = Decimal(0)
    value_max: Decimal = Decimal(0)
    has_range: bool = False
    in_baseline: bool = False
    bought_after: int = 0
    sold_after: int = 0
    partial_sale: bool = False
    latest_evidence: date | None = None


@dataclass(frozen=True)
class ReconstructionStats:
    """Per-run counters for logging."""

    positions: int = 0
    by_certainty: dict[str, int] = field(default_factory=dict)
    unobservable_sales: int = 0


def _baseline_date(fd_filing: Filing) -> date:
    """Baseline as-of date: the FD's filing date, else end of its index year."""
    if fd_filing.filing_date is not None:
        return fd_filing.filing_date
    assert fd_filing.index_year is not None
    return date(fd_filing.index_year, 12, 31)


def reconstruct_member(
    session: Session,
    member_id: int,
    report: MemberDataReport,
    *,
    as_of: date | None = None,
) -> ReconstructionStats:
    """Rebuild Position rows for one member from its FD + PTR data."""
    groups: dict[str, _GroupState] = {}
    stats_unobservable_sales = 0

    # --- Baseline: latest annual FD holdings -------------------------------
    baseline_date: date | None = None
    if report.fd_filing_id is not None:
        fd_filing = session.get(Filing, report.fd_filing_id)
        assert fd_filing is not None
        baseline_date = _baseline_date(fd_filing)
        holdings = session.exec(
            select(Holding).where(Holding.filing_id == report.fd_filing_id)
        ).all()
        for holding in holdings:
            name, ticker = _holding_name_ticker(session, holding)
            if name is None:
                continue
            key, _ = asset_group_key(name, ticker)
            state = groups.setdefault(key, _GroupState(asset_id=holding.asset_id))
            state.in_baseline = True
            if state.asset_id is None:
                state.asset_id = holding.asset_id
            if holding.value_min is not None and holding.value_max is not None:
                state.value_min += holding.value_min
                state.value_max += holding.value_max
                state.has_range = True
            state.latest_evidence = baseline_date

    # --- PTR overlay --------------------------------------------------------
    ptr_transactions = session.exec(
        select(Transaction)
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .where(Filing.member_id == member_id, Filing.filing_type_raw == "P")
        .order_by(Transaction.transaction_date)  # type: ignore[arg-type]
    ).all()
    for tx in ptr_transactions:
        if baseline_date and tx.transaction_date and tx.transaction_date <= baseline_date:
            continue  # already inside the baseline disclosure
        name, ticker = _transaction_name_ticker(session, tx)
        if name is None:
            continue
        key, _ = asset_group_key(name, ticker)
        group_state = groups.get(key)
        if group_state is None:
            if tx.transaction_type is TransactionType.SALE:
                stats_unobservable_sales += 1
                continue
            group_state = _GroupState(asset_id=tx.asset_id)
            groups[key] = group_state
        if group_state.asset_id is None:
            group_state.asset_id = tx.asset_id
        if tx.transaction_date and (
            group_state.latest_evidence is None
            or tx.transaction_date > group_state.latest_evidence
        ):
            group_state.latest_evidence = tx.transaction_date
        if tx.transaction_type is TransactionType.PURCHASE:
            group_state.bought_after += 1
            # Only full ranges accumulate; open-range purchases are noted via
            # bought_after but add no dishonest bound.
            if (
                not group_state.in_baseline
                and tx.amount_min is not None
                and tx.amount_max is not None
            ):
                group_state.value_min += tx.amount_min
                group_state.value_max += tx.amount_max
                group_state.has_range = True
        elif tx.transaction_type is TransactionType.SALE:
            group_state.sold_after += 1
            if "partial" in tx.raw_text.lower():
                group_state.partial_sale = True

    # --- Emit Position rows --------------------------------------------------
    positions = 0
    by_certainty: dict[str, int] = {}
    effective_as_of = as_of or date.today()
    for state in groups.values():
        if state.asset_id is None:
            continue
        if state.sold_after and not state.in_baseline and not state.bought_after:
            continue  # never observable
        if state.in_baseline and state.sold_after:
            certainty = CertaintyLabel.LOW
            method = METHOD_MAYBE_REDUCED
            if state.partial_sale:
                method += " [partial sale noted]"
        elif state.in_baseline and state.bought_after:
            certainty = CertaintyLabel.MEDIUM
            method = METHOD_CORROBORATED
        elif state.in_baseline:
            certainty = CertaintyLabel.MEDIUM
            method = METHOD_BASELINE
        else:
            certainty = CertaintyLabel.LOW
            method = METHOD_PTR_NEW
        value_min = state.value_min if state.has_range else None
        value_max = state.value_max if state.has_range else None
        session.add(
            Position(
                member_id=member_id,
                asset_id=state.asset_id,
                as_of=state.latest_evidence or effective_as_of,
                value_min=value_min,
                value_max=value_max,
                value_midpoint=midpoint(value_min, value_max),
                certainty=certainty,
                method=method,
            )
        )
        positions += 1
        by_certainty[certainty.value] = by_certainty.get(certainty.value, 0) + 1

    return ReconstructionStats(
        positions=positions,
        by_certainty=by_certainty,
        unobservable_sales=stats_unobservable_sales,
    )


def _holding_name_ticker(session: Session, holding: Holding) -> tuple[str | None, str | None]:
    if holding.asset_id is None:
        return None, None
    asset = session.get(Asset, holding.asset_id)
    if asset is None:
        return None, None
    return asset.name, asset.ticker


def _transaction_name_ticker(
    session: Session, tx: Transaction
) -> tuple[str | None, str | None]:
    if tx.asset_id is None:
        return None, None
    asset = session.get(Asset, tx.asset_id)
    if asset is None:
        return None, None
    return asset.name, asset.ticker
