"""Normalization utilities (M3): midpoints and asset canonicalization.

Rules (per CLAUDE.md): midpoint logic is explicit for disclosed ranges;
low-confidence issuer strings are NEVER silently mapped to tickers.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from app.core.enums import CertaintyLabel


def midpoint(value_min: Decimal | None, value_max: Decimal | None) -> Decimal | None:
    """Explicit midpoint of a disclosed range: (min + max) / 2, rounded to cents.

    Returns None when either bound is missing (open ranges and disclosed-None
    have no honest midpoint).
    """
    if value_min is None or value_max is None:
        return None
    return ((value_min + value_max) / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def canonical_asset_key(name: str) -> str:
    """Canonical form of a verbatim asset name for grouping.

    Lowercase, punctuation stripped, whitespace collapsed. Conservative: this
    only groups textually near-identical names — it is NOT entity resolution.
    """
    cleaned = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def asset_group_key(name: str, ticker: str | None) -> tuple[str, CertaintyLabel]:
    """Grouping key for an asset plus the confidence of that grouping.

    - ticker present (it was explicitly delimited in the source) -> group by
      ticker, HIGH confidence;
    - otherwise group by canonical name, MEDIUM confidence;
    - names that canonically reduce to nothing stay ungrouped, LOW.
    """
    if ticker:
        return (f"ticker:{ticker.upper()}", CertaintyLabel.HIGH)
    canonical = canonical_asset_key(name)
    if canonical:
        return (f"name:{canonical}", CertaintyLabel.MEDIUM)
    return (f"raw:{id(name)}", CertaintyLabel.LOW)
