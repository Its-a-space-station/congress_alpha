"""Notes engine (M4): manual annotations on members and filings.

Service layer only — the dashboard UI for notes is M5. A note must attach
to at least one of a member or a filing.
"""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.db.models import Note


def add_note(
    session: Session,
    body: str,
    *,
    member_id: int | None = None,
    filing_id: int | None = None,
) -> Note:
    """Create a note on a member and/or a filing."""
    if member_id is None and filing_id is None:
        raise ValueError("a note must attach to a member and/or a filing")
    if not body.strip():
        raise ValueError("note body must not be empty")
    note = Note(
        member_id=member_id,
        filing_id=filing_id,
        body=body.strip(),
        created_at=datetime.now(UTC),
    )
    session.add(note)
    session.flush()
    return note


def list_notes(
    session: Session,
    *,
    member_id: int | None = None,
    filing_id: int | None = None,
) -> list[Note]:
    """List notes, optionally filtered by member and/or filing."""
    query = select(Note).order_by(Note.created_at)  # type: ignore[arg-type]
    if member_id is not None:
        query = query.where(Note.member_id == member_id)
    if filing_id is not None:
        query = query.where(Note.filing_id == filing_id)
    return list(session.exec(query).all())
