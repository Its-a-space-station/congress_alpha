"""Net-worth estimation (M3). Conservative bounds + explicit certainty.

Method (assumptions stated per CLAUDE.md):
- Household = the FD household definition: self + spouse + joint + dependent
  rows all count toward the member's household estimate.
- estimate_min = sum(holding minimums) - sum(liability maximums);
  estimate_max = sum(holding maximums) - sum(liability minimums). Partial
  ranges contribute their known bound to both sums (biased to neither side).
  Holdings disclosed as "None" contribute zero.
- Midpoint is the explicit midpoint of those two bounds.
- Certainty: HIGH when the checkpoint shows complete ranges and no warnings;
  MEDIUM when only minor gaps; LOW when material gaps exist; no estimate at
  all without a parsed annual FD (there is no honest basis).
"""

import logging
from decimal import Decimal

from sqlmodel import Session, select

from app.core.enums import CertaintyLabel
from app.db.models import Holding, Liability, NetWorthEstimate
from app.intelligence.checkpoint import MemberDataReport
from app.intelligence.normalize import midpoint

logger = logging.getLogger(__name__)

METHOD = "sum of household holding ranges minus liability ranges (latest annual FD)"


def _certainty(report: MemberDataReport) -> CertaintyLabel:
    """Certainty from checkpoint facts: completeness and anomalies."""
    if report.holdings_partial_range == 0 and not report.warnings:
        return CertaintyLabel.HIGH
    if report.holdings_partial_range <= 2 and len(report.warnings) <= 1:
        return CertaintyLabel.MEDIUM
    return CertaintyLabel.LOW


def estimate_net_worth(
    session: Session, report: MemberDataReport
) -> NetWorthEstimate | None:
    """Build a NetWorthEstimate row for one member, or None without an FD."""
    if report.fd_filing_id is None or report.fd_year is None:
        return None

    holdings = session.exec(
        select(Holding).where(Holding.filing_id == report.fd_filing_id)
    ).all()
    liabilities = session.exec(
        select(Liability).where(Liability.filing_id == report.fd_filing_id)
    ).all()

    zero = Decimal(0)
    asset_min = sum((h.value_min or h.value_max or zero for h in holdings), zero)
    asset_max = sum((h.value_max or h.value_min or zero for h in holdings), zero)
    liab_min = sum((row.value_min or row.value_max or zero for row in liabilities), zero)
    liab_max = sum((row.value_max or row.value_min or zero for row in liabilities), zero)

    estimate_min = asset_min - liab_max
    estimate_max = asset_max - liab_min
    return NetWorthEstimate(
        member_id=report.member_id,
        year=report.fd_year,
        estimate_min=estimate_min,
        estimate_max=estimate_max,
        estimate_midpoint=midpoint(estimate_min, estimate_max),
        certainty=_certainty(report),
        method=METHOD,
    )
