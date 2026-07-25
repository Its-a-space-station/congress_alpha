"""Engine and session management for Congress Alpha.

`get_engine()` builds an engine from settings; `init_db()` creates all
tables (Phase 0 scaffold — migrations come later if needed); `session_scope()`
is the one place sessions are acquired so transaction handling stays uniform.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings
from app.db import models  # noqa: F401  (import registers all tables on the metadata)


def get_engine(db_url: str | None = None) -> Engine:
    """Create an engine for the configured (or given) database URL."""
    url = db_url or get_settings().db_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(db_url: str | None = None) -> None:
    """Create all tables. Idempotent."""
    engine = get_engine(db_url)
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(db_url: str | None = None) -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on error."""
    session = Session(get_engine(db_url))
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
