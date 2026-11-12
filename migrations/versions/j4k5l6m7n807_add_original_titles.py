"""Preserve raw source titles without changing existing display titles.

Revision ID: j4k5l6m7n807
Revises: i3j4k5l6m706
"""
import sqlalchemy as sa
from alembic import op

revision = "j4k5l6m7n807"
down_revision = "i3j4k5l6m706"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("original_title", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("original_title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcripts", "original_title")
    op.drop_column("jobs", "original_title")
