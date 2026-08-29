"""Store expert benchmark reviews for score calibration.

Revision ID: 20260827_0013
Revises: 20260827_0012
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0013"
down_revision: str | Sequence[str] | None = "20260827_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_benchmark_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("benchmark_slug", sa.String(length=100), nullable=False),
        sa.Column("benchmark_version", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("expertise", sa.String(length=32), nullable=False),
        sa.Column("human_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("ai_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("ai_dimension_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score_difference", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("dimension_reviews", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("render_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("human_score >= 1 AND human_score <= 10", name="valid_human_score"),
        sa.CheckConstraint("ai_score >= 1 AND ai_score <= 10", name="valid_ai_score"),
        sa.CheckConstraint(
            "score_difference >= -9 AND score_difference <= 9", name="valid_score_difference"
        ),
        sa.CheckConstraint(
            "expertise IN ('designer', 'marketing_expert', 'creative_director')",
            name="valid_benchmark_reviewer_expertise",
        ),
        sa.CheckConstraint("length(render_checksum) = 64", name="benchmark_render_checksum"),
        sa.ForeignKeyConstraint(["generation_id"], ["post_generations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "benchmark_slug", "benchmark_version", "generation_id", "reviewer_user_id",
            name="uq_post_benchmark_review_submission",
        ),
    )
    op.create_index(
        "ix_post_benchmark_reviews_category", "post_benchmark_reviews", ["category", "created_at"]
    )
    op.create_index(
        "ix_post_benchmark_reviews_scope", "post_benchmark_reviews",
        ["reviewer_user_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_post_benchmark_reviews_scope", table_name="post_benchmark_reviews")
    op.drop_index("ix_post_benchmark_reviews_category", table_name="post_benchmark_reviews")
    op.drop_table("post_benchmark_reviews")
