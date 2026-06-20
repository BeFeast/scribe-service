"""merge_heads_correlation_id_and_transcribe_provider

Revision ID: 31d97f52d359
Revises: h2i3j4k505, h2i3j4k5l605
Create Date: 2026-06-20 10:26:49.813156

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31d97f52d359'
down_revision: Union[str, Sequence[str], None] = ('h2i3j4k505', 'h2i3j4k5l605')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
