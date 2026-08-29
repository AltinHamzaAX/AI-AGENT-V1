"""separate post and campaign conversations

Revision ID: 20260827_0010
Revises: 20260826_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0010"
down_revision: str | None = "20260826_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="post"),
    )
    op.create_check_constraint(
        "valid_conversation_kind", "conversations", "kind IN ('post', 'campaign')"
    )
    op.drop_index("ix_conversations_scope", table_name="conversations")
    op.create_index(
        "ix_conversations_scope",
        "conversations",
        ["user_id", "project_id", "kind", "id"],
    )
    op.alter_column("conversations", "kind", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_conversations_scope", table_name="conversations")
    op.create_index("ix_conversations_scope", "conversations", ["user_id", "project_id", "id"])
    op.drop_constraint("valid_conversation_kind", "conversations", type_="check")
    op.drop_column("conversations", "kind")
