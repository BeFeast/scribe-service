"""Shared pytest fixtures.

Most tests are pure-function and need nothing here. The `db_session` fixture
opens a SQLAlchemy session against `SCRIBE_TEST_DATABASE_URL` and is skipped
unless that env is set — local devs run pure tests by default; CI sets the
URL to a postgres service container.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("SCRIBE_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("SCRIBE_TEST_DATABASE_URL not set — DB tests skipped")
    return url


@pytest.fixture(scope="session")
def engine(test_database_url):
    from sqlalchemy import create_engine

    from scribe.db.models import Base

    eng = create_engine(test_database_url, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
