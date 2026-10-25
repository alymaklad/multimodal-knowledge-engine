"""Alembic environment.

The database URL comes from the application settings, so the CLI and the API always migrate the same
database. When the application runs migrations itself it passes its open connection in
`config.attributes["connection"]`, which keeps a test's SQLite file and the migration on one engine.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.config import get_settings
from app.db.database import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite cannot alter a column in place; batch mode rebuilds the table instead.
        render_as_batch=_url().startswith("sqlite"),
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
