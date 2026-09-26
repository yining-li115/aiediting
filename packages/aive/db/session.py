"""Engine and session handling."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings

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


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def init_db() -> None:
    """Bring the configured database to the current Alembic revision."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
