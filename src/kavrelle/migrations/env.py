"""Alembic environment for Kavrelle."""

from __future__ import annotations

from alembic import context

import kavrelle.models  # noqa: F401 - registers every table on Base.metadata
from kavrelle.db import Base, make_engine

config = context.config
target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or ""
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=connection.dialect.name == "sqlite",
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
