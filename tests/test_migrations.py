from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401 - registers every table
from app.db.database import Base
from app.db.migrate import BASELINE_REVISION, LegacyDatabaseError, alembic_config, migrate_database


@pytest.fixture
def engine(tmp_path: Path):
    database = create_engine(f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}")
    yield database
    database.dispose()


def _schema_drift(engine) -> list:
    with engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)


def _revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _head() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_the_migrations_build_exactly_the_schema_the_models_describe(engine) -> None:
    """Fails when a model changes without a migration: run `alembic revision --autogenerate`."""
    migrate_database(engine)
    assert _revision(engine) == _head()
    assert _schema_drift(engine) == []


def test_migrating_twice_changes_nothing(engine) -> None:
    migrate_database(engine)
    migrate_database(engine)
    assert _revision(engine) == _head()


def test_every_migration_can_be_reversed(engine) -> None:
    migrate_database(engine)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}


def test_a_database_from_create_all_is_adopted_without_losing_rows(engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO collections (user_id, name, created_at, updated_at) VALUES ('u', 'Kept', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        )
    migrate_database(engine)
    assert _revision(engine) == _head()
    assert _schema_drift(engine) == []
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM collections")).scalar_one() == "Kept"


def test_an_older_source_library_database_gains_what_it_was_missing(engine) -> None:
    """A phase 1-3 database: no segments table and no chunk locator columns."""
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE source_segments"))
        connection.execute(text("ALTER TABLE content_embeddings DROP COLUMN locator"))
        connection.execute(text("ALTER TABLE content_embeddings DROP COLUMN position"))

    migrate_database(engine)
    assert "source_segments" in inspect(engine).get_table_names()
    assert {"locator", "position"} <= {column["name"] for column in inspect(engine).get_columns("content_embeddings")}
    assert _schema_drift(engine) == []
    assert _revision(engine) == _head()


def test_a_reels_era_database_is_refused_rather_than_mangled(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE reels (id INTEGER PRIMARY KEY, instagram_url TEXT)"))
    with pytest.raises(LegacyDatabaseError, match="predates the source library"):
        migrate_database(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()


def test_the_baseline_is_the_first_revision() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_revision(BASELINE_REVISION).down_revision is None
