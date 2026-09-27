from __future__ import annotations

import logging


def test_init_db_does_not_disable_host_application_loggers(
    tmp_path,
    monkeypatch,
) -> None:
    """Alembic runs inside API startup and must not silence Uvicorn."""
    import aive.db.session as session_mod
    from aive.config import settings

    logger = logging.getLogger("uvicorn.error")
    previous_disabled = logger.disabled
    logger.disabled = False
    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path / 'startup.db'}")

    try:
        session_mod.init_db()
        assert logger.disabled is False
    finally:
        logger.disabled = previous_disabled
