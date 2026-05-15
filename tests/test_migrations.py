"""Migration regression test: full upgrade chain on a fresh DB, then downgrade.

Catches the silent-noop class of bug where a new revision is added but never
actually runs against a fresh DB — wrong `down_revision`, duplicate revision
id, or an `upgrade()` body that no-ops. Each such bug would let
`alembic upgrade head` exit 0 while leaving the schema short a column.

Requires SCRIBE_TEST_DATABASE_URL (same as other DB-coupled tests). Skipped
locally when unset; CI provides a Postgres service.
"""
from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _drop_public_schema(eng) -> None:
    """Reset the test DB to a truly empty public schema — kills app tables,
    enum types, sequences, AND alembic_version itself. CASCADE clears FKs."""
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture()
def fresh_db_url(test_database_url, monkeypatch):
    """Yield a URL pointing at a wiped-clean test DB. Also retargets
    `scribe.config.settings.database_url` so `migrations/env.py` picks up
    the test URL when alembic re-imports it on each command."""
    from scribe.config import settings

    monkeypatch.setattr(settings, "database_url", test_database_url)

    eng = create_engine(test_database_url, future=True)
    try:
        _drop_public_schema(eng)
        yield test_database_url
    finally:
        _drop_public_schema(eng)
        eng.dispose()


def _alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _table_names(eng) -> set[str]:
    return set(inspect(eng).get_table_names())


def _column_names(eng, table: str) -> set[str]:
    return {col["name"] for col in inspect(eng).get_columns(table)}


def test_alembic_full_chain_on_fresh_db(fresh_db_url):
    """upgrade head must land jobs + transcripts with every column added by
    every revision in the chain; downgrade base must take the schema back to
    empty. Naming each column explicitly is the point — a silently-skipped
    revision surfaces here as a clear AssertionError instead of a downstream
    NoSuchColumn at runtime."""
    from alembic import command

    cfg = _alembic_config(fresh_db_url)
    eng = create_engine(fresh_db_url, future=True)
    try:
        assert _table_names(eng) == set(), "fresh_db_url must yield an empty schema"

        command.upgrade(cfg, "head")

        tables = _table_names(eng)
        assert "jobs" in tables, f"upgrade head did not create jobs (got {tables})"
        assert "transcripts" in tables, f"upgrade head did not create transcripts (got {tables})"

        jobs_cols = _column_names(eng, "jobs")
        assert "callback_url" in jobs_cols, (
            "jobs.callback_url missing after upgrade head — "
            "revision d1e3f4a5b603 (PR #6) likely silent-noop'd"
        )

        transcripts_cols = _column_names(eng, "transcripts")
        assert "vast_cost" in transcripts_cols, (
            "transcripts.vast_cost missing after upgrade head — "
            "revision c8b2e5f3a402 likely silent-noop'd"
        )

        command.downgrade(cfg, "base")

        # alembic_version may or may not be retained depending on alembic
        # version; either way no app tables must remain.
        remaining = _table_names(eng) - {"alembic_version"}
        assert remaining == set(), f"downgrade base left tables behind: {remaining}"
    finally:
        eng.dispose()
