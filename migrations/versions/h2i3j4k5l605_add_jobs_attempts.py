"""add jobs.attempts transient-retry counter

Revision ID: h2i3j4k5l605
Revises: g1h2i3j4k504
Create Date: 2026-06-11 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h2i3j4k5l605"
down_revision: str | Sequence[str] | None = "g1h2i3j4k504"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("jobs", "attempts")
