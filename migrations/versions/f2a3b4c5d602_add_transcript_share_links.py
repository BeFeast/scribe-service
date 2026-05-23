"""add managed transcript share links

Revision ID: f2a3b4c5d602
Revises: f1a2b3c4d5e6
Create Date: 2026-05-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d602"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    target_kind = sa.Enum(
        "page",
        "summary_markdown",
        "transcript_markdown",
        name="share_target_kind",
    )
    op.create_table(
        "transcript_share_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.Text(), nullable=False),
        sa.Column("transcript_id", sa.Integer(), nullable=False),
        sa.Column("target_kind", target_kind, nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("recipient_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transcript_share_links_token_hash", "transcript_share_links", ["token_hash"], unique=True)
    op.create_index("ix_transcript_share_links_transcript_id", "transcript_share_links", ["transcript_id"])
    op.create_index("ix_transcript_share_links_created_by", "transcript_share_links", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_transcript_share_links_created_by", table_name="transcript_share_links")
    op.drop_index("ix_transcript_share_links_transcript_id", table_name="transcript_share_links")
    op.drop_index("ix_transcript_share_links_token_hash", table_name="transcript_share_links")
    op.drop_table("transcript_share_links")
    sa.Enum(name="share_target_kind").drop(op.get_bind(), checkfirst=True)
