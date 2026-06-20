"""add jobs.correlation_id for request tracing

Revision ID: h2i3j4k505
Revises: g1h2i3j4k504
Create Date: 2026-06-20 01:00:00.000000

Stores the API-ingress correlation ID (inbound X-Request-ID or generated)
on each job so every pipeline stage log line and the webhook delivery can
carry a stable trace key from submission through terminal status (#357).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h2i3j4k505"
down_revision: str | Sequence[str] | None = "g1h2i3j4k504"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("correlation_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "correlation_id")