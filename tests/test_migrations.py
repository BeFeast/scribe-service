"""Migration regression test: full upgrade chain on a fresh DB, then downgrade.

Catches the silent-noop class of bug where a new revision is added but never
actually runs against a fresh DB — wrong `down_revision`, duplicate revision
id, or an `upgrade()` body that no-ops. Each such bug would let
`alembic upgrade head` exit 0 while leaving the schema short a column.

Requires SCRIBE_TEST_DATABASE_URL (same as other DB-coupled tests). Skipped
locally when unset; CI provides a Postgres service.

Isolation note: this test runs against a dedicated `{base}_migrations`
Postgres database that the fixture CREATEs and DROPs around the test. It
deliberately does NOT touch the shared test DB used by `conftest.engine`,
because that fixture is session-scoped and runs `Base.metadata.create_all`
exactly once — wiping its DB mid-session (under e.g. pytest-randomly or
explicit ordering) would cascade missing-table failures into unrelated
`db_session` tests.
"""
from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _admin_engine(base_url):
    """Engine against the maintenance `postgres` DB — required to issue
    CREATE/DROP DATABASE, since you cannot drop a DB you are connected to."""
    admin_url = base_url.set(database="postgres")
    return create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")


def _drop_database(admin_eng, name: str) -> None:
    with admin_eng.connect() as conn:
        # Terminate leftover sessions before DROP — a failed prior run can
        # leave one behind, and DROP DATABASE refuses while connections exist.
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))


@pytest.fixture()
def fresh_db_url(test_database_url, monkeypatch):
    """Create a dedicated `{base}_migrations` Postgres DB for this test only,
    yield its URL, and drop the DB on teardown.

    Why a separate DB: the session-scoped `engine` fixture in conftest.py
    runs `Base.metadata.create_all` once per session against the main test
    DB. If this fixture wiped that DB, every later `db_session` test would
    fail with missing-table errors whenever ordering put migration tests
    after the first DB-coupled test (e.g. pytest-randomly).
    """
    from scribe.config import settings

    base_url = make_url(test_database_url)
    if not base_url.database:
        pytest.skip("SCRIBE_TEST_DATABASE_URL has no database name")
    migration_db = f"{base_url.database}_migrations"
    migration_url = base_url.set(database=migration_db)
    migration_url_str = migration_url.render_as_string(hide_password=False)

    admin_eng = _admin_engine(base_url)
    try:
        # IF EXISTS guards against leftovers from a prior crashed run.
        _drop_database(admin_eng, migration_db)
        with admin_eng.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{migration_db}"'))

        # env.py reads settings.database_url at command time — see the
        # comment in _alembic_config for why this monkeypatch is the
        # actual URL propagation mechanism.
        monkeypatch.setattr(settings, "database_url", migration_url_str)
        try:
            yield migration_url_str
        finally:
            _drop_database(admin_eng, migration_db)
    finally:
        admin_eng.dispose()


def _alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    # NOTE: migrations/env.py runs `config.set_main_option("sqlalchemy.url",
    # settings.database_url)` on every alembic command invocation, so the
    # line below is overwritten before each upgrade/downgrade. URL
    # propagation actually relies on the monkeypatch in `fresh_db_url`.
    # Kept here as a belt-and-suspenders default for any future code path
    # that reads the config without going through env.py.
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _table_names(eng) -> set[str]:
    return set(inspect(eng).get_table_names())


def _column_names(eng, table: str) -> set[str]:
    return {col["name"] for col in inspect(eng).get_columns(table)}


def test_alembic_full_chain_on_fresh_db(fresh_db_url):
    """upgrade head must land jobs + transcripts with every column added by
    every revision in the chain; downgrade base must take the schema back to
    empty. Naming each column (and each nullable change) explicitly is the
    point — a silently-skipped revision surfaces here as a clear
    AssertionError instead of a downstream NoSuchColumn at runtime."""
    from alembic import command

    cfg = _alembic_config(fresh_db_url)
    eng = create_engine(fresh_db_url, future=True)
    try:
        assert _table_names(eng) == set(), "fresh_db_url must yield an empty schema"

        command.upgrade(cfg, "head")

        tables = _table_names(eng)
        assert "jobs" in tables, f"upgrade head did not create jobs (got {tables})"
        assert "transcripts" in tables, f"upgrade head did not create transcripts (got {tables})"
        assert "job_stage_events" in tables, (
            "upgrade head did not create job_stage_events — "
            "revision e4f5a6b7c801 likely silent-noop'd"
        )
        assert "app_config" in tables, f"upgrade head did not create app_config (got {tables})"

        app_config_cols = _column_names(eng, "app_config")
        assert {"key", "value", "updated_at"}.issubset(app_config_cols), (
            "app_config columns missing after upgrade head — "
            "revision e2f4a6b8c901 likely silent-noop'd"
        )

        jobs_cols = _column_names(eng, "jobs")
        assert "callback_url" in jobs_cols, (
            "jobs.callback_url missing after upgrade head — "
            "revision d1e3f4a5b603 (PR #6) likely silent-noop'd"
        )
        assert "title" in jobs_cols, (
            "jobs.title missing after upgrade head — "
            "revision a1b2c3d4e5f6 likely silent-noop'd"
        )

        transcripts_cols = _column_names(eng, "transcripts")
        assert "vast_cost" in transcripts_cols, (
            "transcripts.vast_cost missing after upgrade head — "
            "revision c8b2e5f3a402 likely silent-noop'd"
        )
        assert "short_description" in transcripts_cols, (
            "transcripts.short_description missing after upgrade head — "
            "revision f3a9b7c2d104 likely silent-noop'd"
        )

        # Revision a7c1d3e4f201 relaxes transcripts.summary_md to nullable.
        # Asserting column presence alone would miss a silent-noop of that
        # revision (the column existed already with NOT NULL) — check the
        # nullable flag explicitly.
        summary_md_col = next(
            (c for c in inspect(eng).get_columns("transcripts") if c["name"] == "summary_md"),
            None,
        )
        assert summary_md_col is not None, (
            "transcripts.summary_md missing after upgrade head — "
            "revision a7c1d3e4f201 likely silent-noop'd"
        )
        assert summary_md_col["nullable"], (
            "transcripts.summary_md should be nullable after upgrade head — "
            "revision a7c1d3e4f201 likely silent-noop'd"
        )

        stage_cols = _column_names(eng, "job_stage_events")
        assert {"job_id", "stage", "started_at", "finished_at"} <= stage_cols, (
            "job_stage_events missing expected columns — "
            "revision e4f5a6b7c801 likely silent-noop'd"
        )

        command.downgrade(cfg, "base")

        # alembic_version may or may not be retained depending on alembic
        # version; either way no app tables must remain.
        remaining = _table_names(eng) - {"alembic_version"}
        assert remaining == set(), f"downgrade base left tables behind: {remaining}"
    finally:
        eng.dispose()
