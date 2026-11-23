"""Store the cost and outcome of a Vast market transcription attempt."""

import sqlalchemy as sa
from alembic import op

revision = "m7n8o9p0q010"
down_revision = "l6m7n8o9p009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_stage_events", sa.Column("details", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_stage_events", "details")
