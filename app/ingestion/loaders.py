"""Load stage: idempotent upserts of validated records into the database.

Upserts are keyed on stable upstream identifiers (bioguide_id, committee
code), so re-running an ingestion job converges to `unchanged` instead of
duplicating rows. Standard counters are returned for logging.
"""

import logging

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import Committee, CommitteeMembership, Filing, Member
from app.ingestion.records import (
    CommitteeRecord,
    Counters,
    MemberRecord,
    MembershipRecord,
)

logger = logging.getLogger(__name__)


def upsert_members(session: Session, records: list[MemberRecord], source_url: str) -> Counters:
    """Upsert members by bioguide_id; count new/changed/unchanged."""
    counters = Counters(total=len(records))
    for record in records:
        existing = session.exec(
            select(Member).where(Member.bioguide_id == record.bioguide_id)
        ).first()
        values = {
            "first_name": record.first_name,
            "last_name": record.last_name,
            "chamber": record.chamber,
            "party": record.party,
            "state": record.state,
            "district": record.district,
            "in_office_start": record.in_office_start,
            "in_office_end": record.in_office_end,
            "source_url": source_url,
        }
        if existing is None:
            session.add(Member(bioguide_id=record.bioguide_id, **values))
            counters.new += 1
        elif any(getattr(existing, field) != value for field, value in values.items()):
            for field, value in values.items():
                setattr(existing, field, value)
            counters.changed += 1
        else:
            counters.unchanged += 1
    return counters


def upsert_committees(
    session: Session, records: list[CommitteeRecord], source_url: str
) -> Counters:
    """Upsert committees by code; count new/changed/unchanged."""
    counters = Counters(total=len(records))
    for record in records:
        existing = session.exec(select(Committee).where(Committee.code == record.code)).first()
        values = {
            "name": record.name,
            "chamber": record.chamber,
            "source_url": source_url,
        }
        if existing is None:
            session.add(Committee(code=record.code, **values))
            counters.new += 1
        elif any(getattr(existing, field) != value for field, value in values.items()):
            for field, value in values.items():
                setattr(existing, field, value)
            counters.changed += 1
        else:
            counters.unchanged += 1
    return counters


def upsert_memberships(
    session: Session, records: list[MembershipRecord], source_url: str
) -> Counters:
    """Upsert member-committee links; skip records with unknown endpoints."""
    counters = Counters(total=len(records))
    committee_ids = {c.code: c.id for c in session.exec(select(Committee)).all()}
    member_ids = {m.bioguide_id: m.id for m in session.exec(select(Member)).all()}
    seen: set[tuple[int, int]] = set()

    for record in records:
        committee_id = committee_ids.get(record.committee_code)
        member_id = member_ids.get(record.bioguide_id)
        if committee_id is None or member_id is None:
            counters.skipped += 1
            logger.debug(
                "skipping membership %s/%s: unknown committee or member",
                record.committee_code,
                record.bioguide_id,
            )
            continue
        key = (member_id, committee_id)
        if key in seen:
            counters.skipped += 1
            continue
        seen.add(key)
        existing = session.exec(
            select(CommitteeMembership).where(
                CommitteeMembership.member_id == member_id,
                CommitteeMembership.committee_id == committee_id,
            )
        ).first()
        if existing is None:
            session.add(
                CommitteeMembership(
                    member_id=member_id, committee_id=committee_id, source_url=source_url
                )
            )
            counters.new += 1
        else:
            counters.unchanged += 1
    return counters


def table_counts(session: Session) -> dict[str, int]:
    """Row-count validation stage: current row counts per loaded table."""
    return {
        "member": int(session.exec(select(func.count()).select_from(Member)).one()),
        "committee": int(session.exec(select(func.count()).select_from(Committee)).one()),
        "committee_membership": int(
            session.exec(select(func.count()).select_from(CommitteeMembership)).one()
        ),
        "filing": int(session.exec(select(func.count()).select_from(Filing)).one()),
    }
