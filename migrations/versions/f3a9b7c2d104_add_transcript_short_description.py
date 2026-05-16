"""add transcript short descriptions for library cards

Revision ID: f3a9b7c2d104
Revises: e2f4a6b8c901
Create Date: 2026-05-16 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9b7c2d104"
down_revision: str | Sequence[str] | None = "e2f4a6b8c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("short_description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "short_description")
