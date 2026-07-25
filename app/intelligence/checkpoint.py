"""Input-validation checkpoint (M3). Runs before any estimation, per member.

Every certainty label emitted by the position reconstruction and net-worth
estimator traces back to the inputs computed here — see tasks/plan.md §3-M3.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.db.models import Filing, Holding, Liability, Transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemberDataReport:
    """Validation facts about one member's parsed disclosure data."""

    member_id: int
    fd_filing_id: int | None  # latest parsed annual FD filing, if any
    fd_year: int | None
    holdings_total: int = 0
    holdings_complete: int = 0  # both bounds present or explicitly disclosed None
    holdings_partial_range: int = 0  # exactly one bound — suspicious
    liabilities_total: int = 0
    liabilities_partial_range: int = 0
    transactions_total: int = 0
    transactions_missing_dates: int = 0
    transactions_inverted_ranges: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def check_member(session: Session, member_id: int) -> MemberDataReport:
    """Validate one member's parsed data and return the facts for certainty labels."""
    warnings: list[str] = []

    fd_filing = session.exec(
        select(Filing)
        .where(Filing.member_id == member_id, Filing.filing_type_raw == "A")
        .order_by(Filing.index_year.desc())  # type: ignore[union-attr]
    ).first()
    if fd_filing is None:
        warnings.append("no annual FD filing parsed for this member")

    holdings: Sequence[Holding] = []
    liabilities: Sequence[Liability] = []
    if fd_filing is not None and fd_filing.id is not None:
        holdings = session.exec(
            select(Holding).where(Holding.filing_id == fd_filing.id)
        ).all()
        liabilities = session.exec(
            select(Liability).where(Liability.filing_id == fd_filing.id)
        ).all()

    holdings_complete = 0
    holdings_partial = 0
    for holding in holdings:
        bounds = (holding.value_min is not None, holding.value_max is not None)
        if bounds == (True, True) or bounds == (False, False):
            holdings_complete += 1  # full range, or explicitly disclosed None
        else:
            holdings_partial += 1
    liabilities_partial = sum(
        1
        for liab in liabilities
        if (liab.value_min is not None) != (liab.value_max is not None)
    )

    transactions = session.exec(
        select(Transaction)
        .join(Filing, Transaction.filing_id == Filing.id)  # type: ignore[arg-type]
        .where(Filing.member_id == member_id)
    ).all()
    missing_dates = sum(1 for tx in transactions if tx.transaction_date is None)
    inverted = sum(
        1
        for tx in transactions
        if tx.amount_min is not None
        and tx.amount_max is not None
        and tx.amount_min > tx.amount_max
    )
    if holdings_partial:
        warnings.append(f"{holdings_partial} holdings with partial value ranges")
    if liabilities_partial:
        warnings.append(f"{liabilities_partial} liabilities with partial amount ranges")
    if missing_dates:
        warnings.append(f"{missing_dates} transactions missing dates")
    if inverted:
        warnings.append(f"{inverted} transactions with min > max")

    return MemberDataReport(
        member_id=member_id,
        fd_filing_id=fd_filing.id if fd_filing else None,
        fd_year=fd_filing.index_year if fd_filing else None,
        holdings_total=len(holdings),
        holdings_complete=holdings_complete,
        holdings_partial_range=holdings_partial,
        liabilities_total=len(liabilities),
        liabilities_partial_range=liabilities_partial,
        transactions_total=len(transactions),
        transactions_missing_dates=missing_dates,
        transactions_inverted_ranges=inverted,
        warnings=tuple(warnings),
    )
