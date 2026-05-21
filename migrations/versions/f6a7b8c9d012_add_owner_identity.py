"""add owner identity to jobs and transcripts

Revision ID: f6a7b8c9d012
Revises: a1b2c3d4e5f6
Create Date: 2026-05-21 00:00:00.000000
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d012"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("jobs", "transcripts"):
        op.add_column(table, sa.Column("owner_subject", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("owner_email", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("owner_display_name", sa.Text(), nullable=True))
        op.create_index(op.f(f"ix_{table}_owner_subject"), table, ["owner_subject"], unique=False)

    bind = op.get_bind()
    config_rows = dict(
        bind.execute(
            sa.text(
                "SELECT key, value FROM app_config "
                "WHERE key IN ('default_owner_subject', 'default_owner_email')"
            )
        ).all()
    )
    subject = os.getenv("SCRIBE_DEFAULT_OWNER_SUBJECT", "").strip()
    subject = subject or str(config_rows.get("default_owner_subject", "")).strip()
    email = os.getenv("SCRIBE_DEFAULT_OWNER_EMAIL", "").strip()
    email = email or str(config_rows.get("default_owner_email", "")).strip()
    default_subject = subject or email
    if default_subject:
        for table in ("jobs", "transcripts"):
            bind.execute(
                sa.text(
                    f"""
                    UPDATE {table}
                    SET
                        owner_subject = COALESCE(owner_subject, :subject),
                        owner_email = COALESCE(owner_email, NULLIF(:email, ''))
                    WHERE owner_subject IS NULL
                    """
                ),
                {"subject": default_subject, "email": email},
            )


def downgrade() -> None:
    for table in ("transcripts", "jobs"):
        op.drop_index(op.f(f"ix_{table}_owner_subject"), table_name=table)
        op.drop_column(table, "owner_display_name")
        op.drop_column(table, "owner_email")
        op.drop_column(table, "owner_subject")
