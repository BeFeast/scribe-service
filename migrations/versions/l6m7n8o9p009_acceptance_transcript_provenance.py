"""Record the originating acceptance run on fixture jobs and transcripts."""

import sqlalchemy as sa
from alembic import op

revision = "l6m7n8o9p009"
down_revision = "k5l6m7n8o908"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("acceptance_run_id", sa.Text(), nullable=True))
    op.add_column("transcripts", sa.Column("acceptance_run_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcripts", "acceptance_run_id")
    op.drop_column("jobs", "acceptance_run_id")
