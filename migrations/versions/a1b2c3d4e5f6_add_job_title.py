"""add jobs.title for in-flight queue display

Revision ID: a1b2c3d4e5f6
Revises: f3a9b7c2d104
Create Date: 2026-05-16 20:15:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f3a9b7c2d104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "title")
