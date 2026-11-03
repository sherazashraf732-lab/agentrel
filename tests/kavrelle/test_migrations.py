"""The migration scripts must build exactly the schema the models describe."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect

from kavrelle import migrate
from kavrelle.db import Base, make_engine


def test_upgrade_creates_every_table(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)


def test_database_is_at_head(engine: Engine) -> None:
    assert migrate.head_revision() is not None
    assert migrate.current_revision(engine) == migrate.head_revision()


def test_no_drift_between_models_and_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models changed without a migration: {diff}"


def test_upgrade_is_repeatable(db_url: str) -> None:
    migrate.upgrade(db_url)
    migrate.upgrade(db_url)
    eng = make_engine(db_url)
    assert migrate.current_revision(eng) == migrate.head_revision()
    eng.dispose()


def test_downgrade_removes_everything(db_url: str) -> None:
    migrate.upgrade(db_url)
    migrate.downgrade(db_url, "base")
    eng = make_engine(db_url)
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
    eng.dispose()
