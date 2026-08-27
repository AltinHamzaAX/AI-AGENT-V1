"""Add Vision Critic evidence to persisted Posts workflow state.

Revision ID: 20260827_0012
Revises: 20260827_0011
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0012"
down_revision: str | Sequence[str] | None = "20260827_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE post_generation_states
        SET state = state || '{"vision_quality": {}}'::jsonb,
            schema_version = 8
        WHERE NOT (state ? 'vision_quality')
        """
    )
    op.execute(
        """
        UPDATE post_generation_state_versions
        SET state = state || '{"vision_quality": {}}'::jsonb,
            schema_version = 8
        WHERE NOT (state ? 'vision_quality')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE post_generation_states
        SET state = state - 'vision_quality',
            schema_version = 7
        WHERE schema_version = 8
        """
    )
    op.execute(
        """
        UPDATE post_generation_state_versions
        SET state = state - 'vision_quality',
            schema_version = 7
        WHERE schema_version = 8
        """
    )
