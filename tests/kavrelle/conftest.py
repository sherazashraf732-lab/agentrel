"""Shared fixtures. Every test runs on SQLite; set KAVRELLE_TEST_POSTGRES_URL to also run on PostgreSQL.

The PostgreSQL database named there is wiped by the tests: point it at a throwaway
database (for example kavrelle_test), never at real data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from kavrelle import migrate
from kavrelle.db import make_engine, make_session_factory

PG_URL = os.environ.get("KAVRELLE_TEST_POSTGRES_URL", "")
BACKENDS = ["sqlite", *(["postgres"] if PG_URL else [])]


def _reset_postgres(url: str) -> None:
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(params=BACKENDS)
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        _reset_postgres(PG_URL)
        return PG_URL
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    migrate.upgrade(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture(autouse=True)
def _private_outbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep test emails out of the working folder: every test gets its own outbox."""
    monkeypatch.setenv("KAVRELLE_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.delenv("KAVRELLE_SMTP_HOST", raising=False)
