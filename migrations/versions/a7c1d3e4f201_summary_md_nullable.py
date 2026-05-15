"""summary_md nullable for partial transcripts

Revision ID: a7c1d3e4f201
Revises: 923537e8b936
Create Date: 2026-05-15 11:00:00.000000

A transcript may now be persisted with `summary_md IS NULL` immediately after a
successful whisper run — the row "locks in" the expensive GPU work even if the
subsequent codex summary step fails. Once codex returns, the row is updated
with the summary; until then the API treats this transcript as partial (it is
excluded from POST /jobs dedup and from the home-page list).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c1d3e4f201"
down_revision: Union[str, Sequence[str], None] = "923537e8b936"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "transcripts",
        "summary_md",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill any partials to empty string before re-imposing NOT NULL,
    # so this migration is reversible even after partials have been written.
    op.execute("UPDATE transcripts SET summary_md = '' WHERE summary_md IS NULL")
    op.alter_column(
        "transcripts",
        "summary_md",
        existing_type=sa.Text(),
        nullable=False,
    )
