"""Typed records parsed and validated from congress-legislators YAML.

This is the "deterministic filters" stage of the M1 ETL: raw YAML structures
go in, validated dataclasses come out. Malformed entries are skipped with a
logged reason — never silently coerced.
"""

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from app.core.enums import Chamber

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemberRecord:
    """One current member, reduced to the fields the Member table stores."""

    bioguide_id: str
    first_name: str
    last_name: str
    official_full: str | None
    chamber: Chamber
    party: str
    state: str
    district: str | None
    in_office_start: date | None
    in_office_end: date | None


@dataclass(frozen=True)
class CommitteeRecord:
    """One full committee. Subcommittees are out of scope for M1."""

    code: str  # thomas_id, e.g. "HSAG"
    name: str
    chamber: Chamber


@dataclass(frozen=True)
class MembershipRecord:
    """One member-on-committee link, keyed by upstream identifiers."""

    committee_code: str
    bioguide_id: str


@dataclass
class Counters:
    """Standard run counters emitted by every ingestion run."""

    total: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0

    def summary(self) -> str:
        """One-line log rendering of the counters."""
        return (
            f"total={self.total} new={self.new} changed={self.changed} "
            f"unchanged={self.unchanged} skipped={self.skipped}"
        )


_TERM_TYPE_TO_CHAMBER = {"rep": Chamber.HOUSE, "sen": Chamber.SENATE}
_COMMITTEE_TYPE_TO_CHAMBER = {
    "house": Chamber.HOUSE,
    "senate": Chamber.SENATE,
    "joint": Chamber.JOINT,
}


def _read_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def parse_members(path: Path) -> tuple[list[MemberRecord], int]:
    """Parse legislators YAML into MemberRecords.

    Dataset convention: for current legislators the last entry in `terms` is
    the current term. Returns (records, skipped_count).
    """
    raw = _read_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of legislators")

    records: list[MemberRecord] = []
    skipped = 0
    for entry in raw:
        try:
            bioguide = str(entry["id"]["bioguide"])
            first = str(entry["name"]["first"])
            last = str(entry["name"]["last"])
            terms = entry["terms"]
            if not terms:
                raise KeyError("terms")
            current = terms[-1]
            chamber = _TERM_TYPE_TO_CHAMBER[str(current["type"])]
            district = current.get("district")
            records.append(
                MemberRecord(
                    bioguide_id=bioguide,
                    first_name=first,
                    last_name=last,
                    official_full=(
                        str(entry["name"]["official_full"])
                        if entry["name"].get("official_full")
                        else None
                    ),
                    chamber=chamber,
                    party=str(current.get("party", "")),
                    state=str(current["state"]),
                    district=None if district is None else str(district),
                    in_office_start=_parse_date(current.get("start")),
                    in_office_end=_parse_date(current.get("end")),
                )
            )
        except (KeyError, TypeError, AttributeError) as exc:
            skipped += 1
            name = entry.get("name") if isinstance(entry, dict) else None
            logger.warning("skipping legislator %r: %s", name, exc)
    return records, skipped


def parse_committees(path: Path) -> tuple[list[CommitteeRecord], int]:
    """Parse committees YAML into CommitteeRecords (full committees only)."""
    raw = _read_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of committees")

    records: list[CommitteeRecord] = []
    skipped = 0
    for entry in raw:
        try:
            code = str(entry["thomas_id"])
            name = str(entry["name"])
            chamber = _COMMITTEE_TYPE_TO_CHAMBER[str(entry["type"])]
            records.append(CommitteeRecord(code=code, name=name, chamber=chamber))
        except (KeyError, TypeError) as exc:
            skipped += 1
            logger.warning("skipping committee entry: %s", exc)
    return records, skipped


def parse_membership(
    path: Path, valid_committee_codes: set[str]
) -> tuple[list[MembershipRecord], int]:
    """Parse committee-membership YAML into MembershipRecords.

    The file is keyed by committee code and also contains subcommittee keys
    (parent code + subcommittee number). Only keys exactly matching a known
    full-committee code are ingested in M1; the rest are skipped and counted.
    """
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping of committee code -> members")

    records: list[MembershipRecord] = []
    skipped = 0
    for code_value, members in raw.items():
        code = str(code_value)
        if code not in valid_committee_codes:
            skipped += 1
            continue
        if not isinstance(members, list):
            skipped += 1
            logger.warning("skipping membership for %s: expected a list", code)
            continue
        for member in members:
            bioguide = member.get("bioguide") if isinstance(member, dict) else None
            if not bioguide:
                skipped += 1
                logger.warning("skipping membership entry on %s: missing bioguide", code)
                continue
            records.append(MembershipRecord(committee_code=code, bioguide_id=str(bioguide)))
    return records, skipped
