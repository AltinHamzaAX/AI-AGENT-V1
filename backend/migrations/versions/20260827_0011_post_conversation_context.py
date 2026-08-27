"""Persist accumulated Posts chat context per conversation.

Revision ID: 20260827_0011
Revises: 20260827_0010
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0011"
down_revision: str | Sequence[str] | None = "20260827_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_conversation_contexts",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="positive_context_version"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_post_conversation_contexts_scope",
        "post_conversation_contexts",
        ["user_id", "project_id", "conversation_id"],
    )
    op.alter_column("post_conversation_contexts", "version", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_post_conversation_contexts_scope",
        table_name="post_conversation_contexts",
    )
    op.drop_table("post_conversation_contexts")
