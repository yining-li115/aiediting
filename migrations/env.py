"""Alembic environment.

``init_db()`` delegates here for both fresh and existing databases. Autogenerate
compares against ``Base.metadata`` so models stay the single schema definition.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from aive.config import settings
from aive.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.db_url)

if config.config_file_name is not None:
    # The API runs migrations inside Uvicorn's lifespan. Alembic's default
    # ``disable_existing_loggers=True`` silences Uvicorn after the migration,
    # hiding both "startup complete" and request logs even though the server
    # is healthy. Keep the host application's loggers intact.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Needed for SQLite: it cannot ALTER most columns in place.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
