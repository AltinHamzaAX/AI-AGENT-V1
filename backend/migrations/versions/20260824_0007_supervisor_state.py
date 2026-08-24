"""Add durable Post Supervisor progress to workflow state.

Revision ID: 20260824_0007
Revises: 20260824_0006
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0007"
down_revision: str | Sequence[str] | None = "20260824_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE post_generation_states
            SET state = jsonb_set(state, '{supervisor}', '{}'::jsonb, true),
                schema_version = 2
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE post_generation_state_versions
            SET state = jsonb_set(state, '{supervisor}', '{}'::jsonb, true),
                schema_version = 2
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE post_generation_states
            SET state = state - 'supervisor',
                schema_version = 1
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE post_generation_state_versions
            SET state = state - 'supervisor',
                schema_version = 1
            """
        )
    )
