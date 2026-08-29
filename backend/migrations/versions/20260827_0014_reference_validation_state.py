"""Add reference originality validation to Posts workflow state.

Revision ID: 20260827_0014
Revises: 20260827_0013
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0014"
down_revision: str | Sequence[str] | None = "20260827_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE post_generation_states
        SET state = state || '{"reference_validation": {}}'::jsonb,
            schema_version = 9
        WHERE NOT (state ? 'reference_validation')
        """
    )
    op.execute(
        """
        UPDATE post_generation_state_versions
        SET state = state || '{"reference_validation": {}}'::jsonb,
            schema_version = 9
        WHERE NOT (state ? 'reference_validation')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE post_generation_states
        SET state = state - 'reference_validation', schema_version = 8
        WHERE schema_version = 9
        """
    )
    op.execute(
        """
        UPDATE post_generation_state_versions
        SET state = state - 'reference_validation', schema_version = 8
        WHERE schema_version = 9
        """
    )
