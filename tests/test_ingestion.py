"""M1 ingestion tests: parse validation, idempotent upserts, row counts."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from app.core.enums import Chamber
from app.db.session import get_engine
from app.ingestion.loaders import (
    table_counts,
    upsert_committees,
    upsert_members,
    upsert_memberships,
)
from app.ingestion.records import parse_committees, parse_members, parse_membership

FIXTURES = Path(__file__).parent / "fixtures"
LEGISLATORS_FIXTURE = FIXTURES / "legislators-current.yaml"
COMMITTEES_FIXTURE = FIXTURES / "committees-current.yaml"
MEMBERSHIP_FIXTURE = FIXTURES / "committee-membership-current.yaml"
SOURCE_URL = "https://example.test/fixture"


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_parse_members_uses_current_term_and_counts_skips() -> None:
    records, skipped = parse_members(LEGISLATORS_FIXTURE)

    assert skipped == 1  # the BROKEN1 entry has no terms
    assert len(records) == 4

    cantwell = next(r for r in records if r.bioguide_id == "C000127")
    assert cantwell.chamber is Chamber.SENATE  # current term is the last one
    assert cantwell.party == "Democrat"
    assert cantwell.state == "WA"
    assert cantwell.district is None
    assert cantwell.in_office_start == date(2025, 1, 3)
    assert cantwell.in_office_end == date(2031, 1, 3)

    ada = next(r for r in records if r.bioguide_id == "A000001")
    assert ada.chamber is Chamber.HOUSE
    assert ada.district == "12"

    bob = next(r for r in records if r.bioguide_id == "B000002")
    assert bob.chamber is Chamber.HOUSE
    assert bob.district is None  # at-large: no district in the dataset


def test_upsert_members_is_idempotent_and_counts_changes(session: Session) -> None:
    records, _ = parse_members(LEGISLATORS_FIXTURE)

    first = upsert_members(session, records, SOURCE_URL)
    assert (first.new, first.changed, first.unchanged) == (4, 0, 0)

    second = upsert_members(session, records, SOURCE_URL)
    assert (second.new, second.changed, second.unchanged) == (0, 0, 4)

    changed_record = replace(records[0], party="Independent")
    third = upsert_members(session, [changed_record], SOURCE_URL)
    assert (third.new, third.changed, third.unchanged) == (0, 1, 0)
    assert table_counts(session)["member"] == 4


def test_committee_ingestion_end_to_end(session: Session) -> None:
    # Members must exist before memberships can be linked (CLI runs
    # `ingest members` before `ingest committees` for the same reason).
    members, _ = parse_members(LEGISLATORS_FIXTURE)
    upsert_members(session, members, SOURCE_URL)

    committees, skipped_committees = parse_committees(COMMITTEES_FIXTURE)
    assert skipped_committees == 1  # entry without thomas_id
    assert len(committees) == 3
    joint = next(c for c in committees if c.code == "JECO")
    assert joint.chamber is Chamber.JOINT

    first = upsert_committees(session, committees, SOURCE_URL)
    assert (first.new, first.changed, first.unchanged) == (3, 0, 0)
    assert upsert_committees(session, committees, SOURCE_URL).unchanged == 3

    memberships, skipped_memberships = parse_membership(
        MEMBERSHIP_FIXTURE, {c.code for c in committees}
    )
    assert skipped_memberships == 1  # HSAG15 subcommittee key is out of M1 scope
    assert len(memberships) == 5

    first_links = upsert_memberships(session, memberships, SOURCE_URL)
    # Z999999 is not a known member -> skipped at load.
    assert (first_links.new, first_links.skipped) == (4, 1)

    second_links = upsert_memberships(session, memberships, SOURCE_URL)
    assert (second_links.new, second_links.unchanged, second_links.skipped) == (0, 4, 1)

    assert table_counts(session) == {
        "member": 4,
        "committee": 3,
        "committee_membership": 4,
        "filing": 0,
    }
