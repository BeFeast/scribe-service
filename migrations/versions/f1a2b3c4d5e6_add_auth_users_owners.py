"""add auth users, owners, and ownership columns

Revision ID: f1a2b3c4d5e6
Revises: f6a7b8c9d012
Create Date: 2026-05-22 08:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "f6a7b8c9d012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("clerk_subject", sa.Text(), nullable=True),
        sa.Column("primary_email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clerk_subject", name="uq_users_clerk_subject"),
        sa.UniqueConstraint("primary_email", name="uq_users_primary_email"),
    )
    op.create_index(op.f("ix_users_owner_id"), "users", ["owner_id"], unique=False)
    op.create_table(
        "extension_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_extension_tokens_hash"),
    )
    op.create_index(op.f("ix_extension_tokens_user_id"), "extension_tokens", ["user_id"], unique=False)
    op.add_column("jobs", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_jobs_owner_id"), "jobs", ["owner_id"], unique=False)
    op.create_foreign_key("fk_jobs_owner_id_owners", "jobs", "owners", ["owner_id"], ["id"], ondelete="SET NULL")
    op.add_column("transcripts", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_transcripts_owner_id"), "transcripts", ["owner_id"], unique=False)
    op.create_foreign_key(
        "fk_transcripts_owner_id_owners",
        "transcripts",
        "owners",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_transcripts_owner_id_owners", "transcripts", type_="foreignkey")
    op.drop_index(op.f("ix_transcripts_owner_id"), table_name="transcripts")
    op.drop_column("transcripts", "owner_id")
    op.drop_constraint("fk_jobs_owner_id_owners", "jobs", type_="foreignkey")
    op.drop_index(op.f("ix_jobs_owner_id"), table_name="jobs")
    op.drop_column("jobs", "owner_id")
    op.drop_index(op.f("ix_extension_tokens_user_id"), table_name="extension_tokens")
    op.drop_table("extension_tokens")
    op.drop_index(op.f("ix_users_owner_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("owners")
