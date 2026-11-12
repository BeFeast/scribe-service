"""add the ai_transcript share kind and share-link access tracking

Revision ID: k5l6m7n8o908
Revises: j4k5l6m7n807
Create Date: 2026-11-12 12:00:00.000000

Copy for AI (#489). Adds the `ai_transcript` value to the share_target_kind
enum and two usage-tracking columns to transcript_share_links:
`access_count` (NOT NULL, server default 0, so `access_count + 1` never
stays NULL) and nullable `last_accessed_at`.

`ALTER TYPE ... ADD VALUE` runs inside alembic's transaction on PostgreSQL
12+ as long as the new label is not used in the same transaction (it is
not). `IF NOT EXISTS` keeps the migration idempotent across partial re-runs.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k5l6m7n8o908"
down_revision: str | Sequence[str] | None = "j4k5l6m7n807"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE share_target_kind ADD VALUE IF NOT EXISTS 'ai_transcript'")
    op.add_column(
        "transcript_share_links",
        sa.Column("access_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "transcript_share_links",
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcript_share_links", "last_accessed_at")
    op.drop_column("transcript_share_links", "access_count")
    # PostgreSQL has no DROP VALUE: drop the ai_transcript links, then rebuild
    # the enum with the three original labels.
    op.execute("DELETE FROM transcript_share_links WHERE target_kind = 'ai_transcript'")
    op.execute("ALTER TYPE share_target_kind RENAME TO share_target_kind_old")
    op.execute(
        "CREATE TYPE share_target_kind AS ENUM ('page', 'summary_markdown', 'transcript_markdown')"
    )
    op.execute(
        "ALTER TABLE transcript_share_links ALTER COLUMN target_kind TYPE share_target_kind "
        "USING target_kind::text::share_target_kind"
    )
    op.execute("DROP TYPE share_target_kind_old")
