"""Create Posts domain persistence models.

Revision ID: 20260824_0004
Revises: 20260824_0003
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0004"
down_revision: str | Sequence[str] | None = "20260824_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_posts_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_posts")),
    )
    op.create_index("ix_posts_campaign", "posts", ["campaign_id", "id"])
    op.create_index("ix_posts_scope", "posts", ["user_id", "project_id", "id"])

    op.create_table(
        "post_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("attempt > 0", name=op.f("ck_post_generations_positive_attempt")),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'reviewing', 'revision', "
            "'completed', 'failed', 'cancelled')",
            name=op.f("ck_post_generations_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["posts.id"],
            name=op.f("fk_post_generations_post_id_posts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_generations")),
        sa.UniqueConstraint(
            "post_id",
            "attempt",
            name=op.f("uq_post_generations_post_attempt"),
        ),
    )
    op.create_index(
        "ix_post_generations_post",
        "post_generations",
        ["post_id", "attempt"],
    )

    op.create_table(
        "generation_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('intermediate', 'preview', 'final')",
            name=op.f("ck_generation_artifacts_valid_kind"),
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_generation_artifacts_positive_size"),
        ),
        sa.CheckConstraint(
            "length(checksum) = 64",
            name=op.f("ck_generation_artifacts_sha256_checksum"),
        ),
        sa.CheckConstraint(
            "(width IS NULL AND height IS NULL) OR "
            "(width IS NOT NULL AND height IS NOT NULL AND width > 0 AND height > 0)",
            name=op.f("ck_generation_artifacts_valid_dimensions"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["post_generations.id"],
            name=op.f("fk_generation_artifacts_generation_id_post_generations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_artifacts")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_generation_artifacts_storage_key")),
    )
    op.create_index(
        "ix_generation_artifacts_generation",
        "generation_artifacts",
        ["generation_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_artifacts_generation", table_name="generation_artifacts")
    op.drop_table("generation_artifacts")
    op.drop_index("ix_post_generations_post", table_name="post_generations")
    op.drop_table("post_generations")
    op.drop_index("ix_posts_scope", table_name="posts")
    op.drop_index("ix_posts_campaign", table_name="posts")
    op.drop_table("posts")
