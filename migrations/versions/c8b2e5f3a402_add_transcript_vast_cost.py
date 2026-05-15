"""add transcripts.vast_cost for daily-spend cap

Revision ID: c8b2e5f3a402
Revises: a7c1d3e4f201
Create Date: 2026-05-15 16:00:00.000000

Records the estimated Vast.ai cost per transcribe so POST /jobs can refuse
new submissions once today's spend exceeds SCRIBE_DAILY_SPEND_CAP_USD.
Nullable: legacy rows + warm-pool runs leave it NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8b2e5f3a402"
down_revision: Union[str, Sequence[str], None] = "a7c1d3e4f201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("vast_cost", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_transcripts_created_at",
        "transcripts",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_transcripts_created_at", table_name="transcripts")
    op.drop_column("transcripts", "vast_cost")
