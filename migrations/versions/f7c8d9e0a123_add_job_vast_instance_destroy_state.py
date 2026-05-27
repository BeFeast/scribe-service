"""add job Vast instance destroy state

Revision ID: f7c8d9e0a123
Revises: f2a3b4c5d602
Create Date: 2026-05-27 00:00:00.000000

Persist the live Vast.ai instance handle on the job row as soon as the
instance is created, and track destroy confirmation failures separately from
pipeline status so restarts can retry cleanup.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7c8d9e0a123"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d602"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("vast_instance_id", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("destroy_failed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "destroy_failed_at")
    op.drop_column("jobs", "vast_instance_id")
