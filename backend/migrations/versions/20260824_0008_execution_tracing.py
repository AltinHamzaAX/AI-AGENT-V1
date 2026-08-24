"""Add durable Posts execution tracing.

Revision ID: 20260824_0008
Revises: 20260824_0007
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0008"
down_revision: str | Sequence[str] | None = "20260824_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_execution_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_reference", sa.String(length=71), nullable=True),
        sa.Column("output_reference", sa.String(length=71), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=300), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=200), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('agent', 'tool', 'provider', 'generation_step')",
            name=op.f("ck_post_execution_traces_valid_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'timeout', 'denied')",
            name=op.f("ck_post_execution_traces_valid_status"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name=op.f("ck_post_execution_traces_non_negative_duration"),
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name=op.f("ck_post_execution_traces_non_negative_retries"),
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_post_execution_traces_non_negative_input_tokens"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_post_execution_traces_non_negative_output_tokens"),
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name=op.f("ck_post_execution_traces_non_negative_cost"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["post_generations.id"],
            name=op.f("fk_post_execution_traces_generation_id_post_generations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_execution_traces")),
    )
    op.create_index(
        "ix_post_execution_traces_generation_timeline",
        "post_execution_traces",
        ["generation_id", "started_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_post_execution_traces_correlation",
        "post_execution_traces",
        ["correlation_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_post_execution_traces_correlation",
        table_name="post_execution_traces",
    )
    op.drop_index(
        "ix_post_execution_traces_generation_timeline",
        table_name="post_execution_traces",
    )
    op.drop_table("post_execution_traces")
