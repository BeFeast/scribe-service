"""add transcript author/platform metadata for Properties surfacing

Revision ID: g1h2i3j4k504
Revises: f7c8d9e0a123
Create Date: 2026-05-30 02:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g1h2i3j4k504"
down_revision: str | Sequence[str] | None = "f7c8d9e0a123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("author_name", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("author_handle", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("author_url", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("source_platform", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcripts", "source_platform")
    op.drop_column("transcripts", "author_url")
    op.drop_column("transcripts", "author_handle")
    op.drop_column("transcripts", "author_name")
