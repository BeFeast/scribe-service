"""add jobs.callback_url for webhook output

Revision ID: d1e3f4a5b603
Revises: c8b2e5f3a402
Create Date: 2026-05-15 18:00:00.000000

scribe pushes the final JobView JSON to this URL on terminal status
(done or failed). Optional; NULL means the consumer wants to poll
GET /jobs/<id> instead.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e3f4a5b603"
down_revision: Union[str, Sequence[str], None] = "c8b2e5f3a402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("callback_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "callback_url")
