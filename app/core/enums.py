"""Explicit enumerations shared across the Congress Alpha codebase.

These enums are the single source of truth for categorical values stored in the
database and used by parsing, intelligence, and scoring code. Keep them aligned
with the terminology guardrails in CLAUDE.md (no accusatory language).
"""

from enum import StrEnum


class Chamber(StrEnum):
    """Legislative chamber a member or committee belongs to."""

    HOUSE = "house"
    SENATE = "senate"
    JOINT = "joint"  # joint committees only; members are always house/senate


class FilingType(StrEnum):
    """Kinds of official financial disclosure filings."""

    ANNUAL = "annual"
    PERIODIC_TRANSACTION = "periodic_transaction"  # PTR
    AMENDMENT = "amendment"
    CANDIDATE = "candidate"
    TERMINATION = "termination"
    EXTENSION = "extension"


class OwnerType(StrEnum):
    """Whose household interest an asset or transaction is disclosed for."""

    SELF = "self"
    SPOUSE = "spouse"
    DEPENDENT_CHILD = "dependent_child"
    JOINT = "joint"
    UNDISCLOSED = "undisclosed"


class AssetType(StrEnum):
    """Asset categories in scope for the MVP (see CLAUDE.md guardrails)."""

    STOCK = "stock"
    STOCK_OPTION = "stock_option"
    OTHER = "other"


class TransactionType(StrEnum):
    """Transaction directions disclosed on PTRs and annual filings."""

    PURCHASE = "purchase"
    SALE = "sale"
    EXCHANGE = "exchange"


class CertaintyLabel(StrEnum):
    """Confidence attached to estimated values (net worth, positions)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ScoreLabel(StrEnum):
    """Human-readable bands for the policy-edge score.

    Deliberately neutral wording per CLAUDE.md: this is a policy-exposure
    overlap signal, not an accusation of wrongdoing.
    """

    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
