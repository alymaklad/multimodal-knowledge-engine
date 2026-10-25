"""Bringing the database to the current schema with Alembic.

The API runs this at startup (unless AUTO_MIGRATE is off), so a local install never needs a separate
step. A deployment with several API processes should turn it off and run `alembic upgrade head` once
per release instead, so two processes never migrate at the same moment.

Databases created before migrations existed were built by `create_all` and carry no revision. Those
from the source-library era (phases 1 to 4) are adopted: the gaps between their schema and the
baseline are known exactly, so they are filled and the database is stamped at the baseline. A
database from the Instagram Reels era predates the source library entirely and is refused.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Column, DateTime, Engine, ForeignKey, Integer, MetaData, String, Table, Text
from sqlalchemy import UniqueConstraint, func, inspect, text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION = "0001"


class LegacyDatabaseError(RuntimeError):
    pass


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    if connection is not None:
        url = connection.engine.url.render_as_string(hide_password=False)
        # Config values go through ConfigParser interpolation, which treats % as special.
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        config.attributes["connection"] = connection
    return config


def migrate_database(engine: Engine) -> None:
    """Apply every pending migration, adopting an unversioned database first if there is one."""
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        config = alembic_config(connection)
        if tables and "alembic_version" not in tables:
            adopt_unversioned_database(connection, tables)
            command.stamp(config, BASELINE_REVISION)
            logger.info("Adopted a database created before migrations; stamped at the baseline")
        command.upgrade(config, "head")


def adopt_unversioned_database(connection: Connection, tables: set[str]) -> None:
    """Fill the known gaps between a phase 1-4 `create_all` database and the baseline.

    The definitions here are frozen copies of the baseline, not the live models, so adoption keeps
    meaning "reach the baseline" however the models change in later revisions.
    """
    if "sources" not in tables:
        raise LegacyDatabaseError(
            "This database predates the source library (it has no 'sources' table). Move it aside, for "
            "example by renaming the SQLite file, and start again; its Reels cannot be migrated."
        )
    if "source_segments" not in tables:
        _baseline_source_segments().create(connection)
    columns = {column["name"] for column in inspect(connection).get_columns("content_embeddings")}
    dialect = connection.dialect
    for name, column_type, default in (("locator", JSON(), "'{}'"), ("position", Integer(), "0")):
        if name not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE content_embeddings ADD COLUMN {name} "
                    f"{column_type.compile(dialect=dialect)} NOT NULL DEFAULT {default}"
                )
            )
    if dialect.name == "postgresql":
        # Phase 2 added a stored-object kind; PostgreSQL enum types need the value added explicitly.
        connection.execute(text("ALTER TYPE storedobjectkind ADD VALUE IF NOT EXISTS 'original'"))


def _baseline_source_segments() -> Table:
    metadata = MetaData()
    # Only so the foreign key can be resolved when the DDL is compiled; it is never created.
    Table("sources", metadata, Column("id", Integer, primary_key=True))
    return Table(
        "source_segments",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("source_id", Integer, ForeignKey("sources.id"), nullable=False, index=True),
        Column("sequence", Integer, nullable=False),
        Column("modality", String(32), nullable=False, index=True),
        Column("text", Text, nullable=False),
        Column("locator", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        UniqueConstraint("source_id", "sequence", name="uq_segment_sequence"),
    )
