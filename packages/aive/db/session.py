"""Engine and session handling."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        url = settings.db_url
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )
        _engine = create_engine(url, future=True)
        _SessionFactory = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def init_db() -> None:
    """Create every table. Alembic takes over once the schema starts moving."""
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
