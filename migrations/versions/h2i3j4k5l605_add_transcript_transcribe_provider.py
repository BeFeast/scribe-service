"""add transcripts.transcribe_provider to record the serving provider

Revision ID: h2i3j4k5l605
Revises: g1h2i3j4k504
Create Date: 2026-06-20 12:00:00.000000

Records which transcription provider (vast / openai / local-whisper) produced
each transcript so the fallback chain (scribe.pipeline.transcribe_providers) is
observable per row. Nullable: legacy rows predate the column.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h2i3j4k5l605"
down_revision: str | Sequence[str] | None = "g1h2i3j4k504"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("transcribe_provider", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "transcribe_provider")
