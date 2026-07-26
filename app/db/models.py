"""Skeletal database models for Congress Alpha (Phase 0).

Schema is intentionally minimal but aligned to the MVP in CLAUDE.md:
members/committees, filings, parsed transactions, reconstructed positions,
net worth estimates, decomposable score runs, and manual notes. Every table
that derives from an external source carries traceability fields
(`source_url`, `parser_version`, raw text / confidence where applicable).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlmodel import Field, SQLModel

from app.core.enums import (
    AssetType,
    CertaintyLabel,
    Chamber,
    FilingType,
    OwnerType,
    ScoreLabel,
    TransactionType,
)


class Member(SQLModel, table=True):
    """A current member of Congress (bioguide id is the stable key)."""

    id: int | None = Field(default=None, primary_key=True)
    bioguide_id: str = Field(index=True, unique=True)
    first_name: str
    last_name: str
    official_full: str | None = None  # e.g. "Rick W. Allen" — used for matching
    chamber: Chamber
    party: str
    state: str
    district: str | None = None  # House only
    in_office_start: date | None = None
    in_office_end: date | None = None
    source_url: str | None = None


class Committee(SQLModel, table=True):
    """A congressional committee; name stored verbatim for later mapping."""

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    chamber: Chamber
    source_url: str | None = None


class CommitteeMembership(SQLModel, table=True):
    """Join table: which member sits on which committee."""

    id: int | None = Field(default=None, primary_key=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    committee_id: int = Field(foreign_key="committee.id", index=True)
    source_url: str | None = None


class Filing(SQLModel, table=True):
    """One official disclosure filing (annual FD, PTR, amendment, ...).

    `member_id` is nullable: filings that cannot be matched to a known member
    with sufficient confidence are still recorded (never force-linked). The
    verbatim upstream type code and filer name are always preserved.
    """

    id: int | None = Field(default=None, primary_key=True)
    member_id: int | None = Field(default=None, foreign_key="member.id", index=True)
    chamber: Chamber
    filing_type: FilingType
    filing_type_raw: str | None = None  # verbatim upstream code, e.g. "P"
    filer_name: str | None = None  # name as it appears in the index
    state_district: str | None = None  # e.g. "AL04"
    index_year: int | None = None  # filing-index year (PDF URL path year)
    filing_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    official_doc_id: str | None = Field(default=None, index=True)
    disclosure_type: str | None = None  # verbatim <DisclosureType> (early index years)
    source_url: str
    local_path: str | None = None  # path under data/raw once downloaded
    checksum: str | None = None
    parser_version: str | None = None


class Asset(SQLModel, table=True):
    """A normalized security. Ticker stays null when confidence is too low
    to map — never silently guessed (see CLAUDE.md parser rules)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    ticker: str | None = Field(default=None, index=True)
    asset_type: AssetType = AssetType.OTHER
    sector: str | None = None


class Transaction(SQLModel, table=True):
    """One parsed transaction row, with raw text and parse confidence."""

    id: int | None = Field(default=None, primary_key=True)
    filing_id: int = Field(foreign_key="filing.id", index=True)
    asset_id: int | None = Field(default=None, foreign_key="asset.id")
    owner: OwnerType = OwnerType.UNDISCLOSED
    transaction_type: TransactionType
    transaction_date: date | None = None
    notification_date: date | None = None  # STOCK Act notification date on PTRs
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    amount_midpoint: Decimal | None = None
    raw_text: str  # preserved verbatim from the source filing
    parse_confidence: float
    parser_version: str


class Holding(SQLModel, table=True):
    """One parsed asset holding from an annual FD assets section (M2c)."""

    id: int | None = Field(default=None, primary_key=True)
    filing_id: int = Field(foreign_key="filing.id", index=True)
    asset_id: int | None = Field(default=None, foreign_key="asset.id")
    account: str | None = None  # investment-vehicle wrapper, e.g. "Fidelity IRA Rollover"
    owner: OwnerType = OwnerType.UNDISCLOSED
    value_min: Decimal | None = None
    value_max: Decimal | None = None
    income_details: str | None = None  # verbatim income column content
    description: str | None = None  # verbatim "D:" annotation
    location: str | None = None  # verbatim "L:" annotation
    raw_text: str
    parse_confidence: float
    parser_version: str


class Liability(SQLModel, table=True):
    """One parsed liability from an annual FD liabilities section (M2c)."""

    id: int | None = Field(default=None, primary_key=True)
    filing_id: int = Field(foreign_key="filing.id", index=True)
    owner: OwnerType = OwnerType.UNDISCLOSED
    creditor_name: str  # verbatim
    liability_type: str | None = None  # verbatim, e.g. "Mortgage on our home"
    date_incurred: str | None = None  # verbatim, e.g. "July 2024"
    value_min: Decimal | None = None
    value_max: Decimal | None = None
    raw_text: str
    parse_confidence: float
    parser_version: str


class Position(SQLModel, table=True):
    """An estimated open position reconstructed from filings (Phase 3)."""

    id: int | None = Field(default=None, primary_key=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    asset_id: int = Field(foreign_key="asset.id", index=True)
    as_of: date
    value_min: Decimal | None = None
    value_max: Decimal | None = None
    value_midpoint: Decimal | None = None
    certainty: CertaintyLabel
    method: str  # human-readable reconstruction method, for auditability


class NetWorthEstimate(SQLModel, table=True):
    """Household net worth estimate from an annual disclosure (Phase 3)."""

    id: int | None = Field(default=None, primary_key=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    year: int
    estimate_min: Decimal | None = None
    estimate_max: Decimal | None = None
    estimate_midpoint: Decimal | None = None
    certainty: CertaintyLabel
    method: str


class ScoreRun(SQLModel, table=True):
    """One execution of the scoring engine (Phase 4), fully versioned."""

    id: int | None = Field(default=None, primary_key=True)
    run_at: datetime
    config_version: str
    parser_version: str


class ScoreComponent(SQLModel, table=True):
    """A single decomposable component of a member's policy-edge score."""

    id: int | None = Field(default=None, primary_key=True)
    score_run_id: int = Field(foreign_key="scorerun.id", index=True)
    member_id: int = Field(foreign_key="member.id", index=True)
    component: str  # e.g. "committee_sector_overlap"
    value: float
    label: ScoreLabel | None = None
    details: str | None = None  # human-readable breakdown


class Note(SQLModel, table=True):
    """Manual annotation attached to a member or filing (Phase 4/5)."""

    id: int | None = Field(default=None, primary_key=True)
    member_id: int | None = Field(default=None, foreign_key="member.id")
    filing_id: int | None = Field(default=None, foreign_key="filing.id")
    body: str
    created_at: datetime
