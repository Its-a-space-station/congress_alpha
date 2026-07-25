"""Database scaffold tests on an in-memory SQLite database."""

from sqlmodel import Session, SQLModel, select

from app.core.enums import Chamber
from app.db.models import Member
from app.db.session import get_engine


def test_create_all_and_member_roundtrip() -> None:
    engine = get_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            Member(
                bioguide_id="A000001",
                first_name="Ada",
                last_name="Example",
                chamber=Chamber.HOUSE,
                party="Independent",
                state="CA",
                district="12",
            )
        )
        session.commit()

        found = session.exec(select(Member).where(Member.bioguide_id == "A000001")).one()
        assert found.chamber is Chamber.HOUSE
        assert found.district == "12"
