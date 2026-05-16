"""add job_stage_events for SPA active job timelines

Revision ID: e4f5a6b7c801
Revises: d1e3f4a5b603
Create Date: 2026-05-16 12:00:00.000000

Stores per-stage start/finish timestamps derived from Job.status transitions.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c801"
down_revision: str | Sequence[str] | None = "d1e3f4a5b603"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_stage_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "stage", name="uq_job_stage_events_job_stage"),
    )
    op.create_index(op.f("ix_job_stage_events_job_id"), "job_stage_events", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_stage_events_job_id"), table_name="job_stage_events")
    op.drop_table("job_stage_events")
