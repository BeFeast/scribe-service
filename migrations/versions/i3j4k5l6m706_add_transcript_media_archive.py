"""add transcript media-archive columns and the archiving job status

Revision ID: i3j4k5l6m706
Revises: 4b9f2a7c1e08
Create Date: 2026-07-09 12:00:00.000000

Upload-your-own-video (#408). Adds the nullable archival-media columns to
transcripts (R2 object key + metadata + soft-failure error) and the new
`archiving` value to the job_status enum. All columns are nullable: URL/YouTube
transcripts never archive media, so legacy and URL rows keep NULLs.

`ALTER TYPE ... ADD VALUE` runs fine inside alembic's transaction on
PostgreSQL 12+ as long as the new label is not *used* in the same transaction
(it is not — no row is written with it here). `IF NOT EXISTS` keeps the
migration idempotent across partial re-runs.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i3j4k5l6m706"
down_revision: str | Sequence[str] | None = "4b9f2a7c1e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'archiving' BEFORE 'done'")
    op.add_column("transcripts", sa.Column("media_object_key", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("media_size_bytes", sa.Integer(), nullable=True))
    op.add_column("transcripts", sa.Column("media_content_type", sa.Text(), nullable=True))
    op.add_column(
        "transcripts",
        sa.Column("media_uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("transcripts", sa.Column("media_error", sa.Text(), nullable=True))


def downgrade() -> None:
    # Columns drop cleanly. The 'archiving' enum label is left in place:
    # PostgreSQL has no DROP VALUE, and the label is harmless once the columns
    # are gone. A full downgrade to base drops the job_status type entirely.
    op.drop_column("transcripts", "media_error")
    op.drop_column("transcripts", "media_uploaded_at")
    op.drop_column("transcripts", "media_content_type")
    op.drop_column("transcripts", "media_size_bytes")
    op.drop_column("transcripts", "media_object_key")
