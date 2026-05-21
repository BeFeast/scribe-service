"""add auth owners, users, and extension tokens

Revision ID: b8c9d012e345
Revises: f6a7b8c9d012
Create Date: 2026-05-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d012e345"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    role = sa.Enum("admin", "user", name="user_role")

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
        sa.Column("role", role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clerk_subject"),
        sa.UniqueConstraint("primary_email"),
    )
    op.create_index(op.f("ix_users_owner_id"), "users", ["owner_id"], unique=False)
    op.create_index(op.f("ix_users_primary_email"), "users", ["primary_email"], unique=False)

    op.create_table(
        "extension_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_prefix", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_extension_tokens_token_prefix"), "extension_tokens", ["token_prefix"], unique=False)
    op.create_index(op.f("ix_extension_tokens_user_id"), "extension_tokens", ["user_id"], unique=False)

    op.add_column("jobs", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_jobs_owner_id_owners", "jobs", "owners", ["owner_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_jobs_owner_id"), "jobs", ["owner_id"], unique=False)
    op.add_column("transcripts", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_transcripts_owner_id_owners", "transcripts", "owners", ["owner_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(op.f("ix_transcripts_owner_id"), "transcripts", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_transcripts_owner_id"), table_name="transcripts")
    op.drop_constraint("fk_transcripts_owner_id_owners", "transcripts", type_="foreignkey")
    op.drop_column("transcripts", "owner_id")
    op.drop_index(op.f("ix_jobs_owner_id"), table_name="jobs")
    op.drop_constraint("fk_jobs_owner_id_owners", "jobs", type_="foreignkey")
    op.drop_column("jobs", "owner_id")
    op.drop_index(op.f("ix_extension_tokens_user_id"), table_name="extension_tokens")
    op.drop_index(op.f("ix_extension_tokens_token_prefix"), table_name="extension_tokens")
    op.drop_table("extension_tokens")
    op.drop_index(op.f("ix_users_primary_email"), table_name="users")
    op.drop_index(op.f("ix_users_owner_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("owners")
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
