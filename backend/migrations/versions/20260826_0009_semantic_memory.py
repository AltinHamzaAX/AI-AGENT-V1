"""Add scoped semantic memory with pgvector retrieval.

Revision ID: 20260826_0009
Revises: 20260824_0008
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0009"
down_revision: str | Sequence[str] | None = "20260824_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_semantic_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scope_level", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=200), nullable=False),
        sa.Column("brand_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("brand_neutral", sa.Boolean(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", VECTOR(dim=768), nullable=False),
        sa.Column("embedding_provider", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=300), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('brand_knowledge', 'approved_creative', 'research_summary', "
            "'successful_concept', 'visual_reference', 'designer_feedback', "
            "'rejected_concept', 'rejected_pattern')",
            name=op.f("ck_post_semantic_memories_valid_kind"),
        ),
        sa.CheckConstraint(
            "scope_level IN ('brand', 'project', 'category', 'global')",
            name=op.f("ck_post_semantic_memories_valid_scope_level"),
        ),
        sa.CheckConstraint(
            "(scope_level = 'brand' AND brand_id IS NOT NULL AND project_id IS NULL "
            "AND category IS NULL AND brand_neutral = false) OR "
            "(scope_level = 'project' AND project_id IS NOT NULL AND brand_id IS NULL "
            "AND category IS NULL AND brand_neutral = false) OR "
            "(scope_level = 'category' AND category IS NOT NULL AND brand_id IS NULL "
            "AND project_id IS NULL AND brand_neutral = true) OR "
            "(scope_level = 'global' AND brand_id IS NULL AND project_id IS NULL "
            "AND category IS NULL AND brand_neutral = true)",
            name=op.f("ck_post_semantic_memories_valid_scope_selectors"),
        ),
        sa.CheckConstraint(
            "length(content) > 0",
            name=op.f("ck_post_semantic_memories_non_empty_content"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name=op.f("ck_post_semantic_memories_sha256_content_hash"),
        ),
        sa.CheckConstraint(
            "embedding_dimension = 768",
            name=op.f("ck_post_semantic_memories_embedding_dimension"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_semantic_memories")),
        sa.UniqueConstraint(
            "user_id",
            "scope_level",
            "scope_key",
            "kind",
            "content_hash",
            name="uq_post_semantic_memories_partition_content",
        ),
    )
    op.create_index(
        "ix_post_semantic_memories_partition",
        "post_semantic_memories",
        ["user_id", "scope_level", "scope_key", "kind"],
        unique=False,
    )
    op.execute(
        "CREATE INDEX ix_post_semantic_memories_embedding_hnsw "
        "ON post_semantic_memories USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_post_semantic_memories_embedding_hnsw",
        table_name="post_semantic_memories",
    )
    op.drop_index("ix_post_semantic_memories_partition", table_name="post_semantic_memories")
    op.drop_table("post_semantic_memories")
