"""add jobs.summarize / jobs.notify / jobs.summary_prompt (per-job toggles)

Revision ID: 4b9f2a7c1e08
Revises: 31d97f52d359
Create Date: 2026-06-20 00:00:00.000000

Per-job pipeline toggles for the mobile Capture sheet (#296):
  * summarize      — gate the codex summary step (False = transcript-only).
  * notify         — gate terminal-status webhook delivery.
  * summary_prompt — optional override of the active prompt template.

summarize/notify are backfilled to TRUE on existing rows so the historical
"always summarize, always deliver webhook" behavior is preserved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4b9f2a7c1e08"
down_revision: Union[str, Sequence[str], None] = "31d97f52d359"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "summarize",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "notify",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("summary_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "summary_prompt")
    op.drop_column("jobs", "notify")
    op.drop_column("jobs", "summarize")
