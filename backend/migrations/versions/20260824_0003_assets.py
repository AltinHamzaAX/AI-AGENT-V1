"""Create scoped message assets.

Revision ID: 20260824_0003
Revises: 20260824_0002
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0003"
down_revision: str | Sequence[str] | None = "20260824_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("height > 0", name=op.f("ck_assets_positive_height")),
        sa.CheckConstraint("length(checksum) = 64", name=op.f("ck_assets_sha256_checksum")),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_assets_positive_size")),
        sa.CheckConstraint(
            "role IN ('logo', 'product', 'vehicle', 'packaging', 'environment', "
            "'background', 'person', 'style_reference', 'inspiration', 'supporting_asset')",
            name=op.f("ck_assets_valid_role"),
        ),
        sa.CheckConstraint("width > 0", name=op.f("ck_assets_positive_width")),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_assets_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("message_id", "checksum", name=op.f("uq_assets_message_checksum")),
    )
    op.create_index("ix_assets_scope", "assets", ["user_id", "project_id", "id"])
    op.create_index(
        "ix_assets_scope_checksum",
        "assets",
        ["user_id", "project_id", "checksum"],
    )


def downgrade() -> None:
    op.drop_index("ix_assets_scope_checksum", table_name="assets")
    op.drop_index("ix_assets_scope", table_name="assets")
    op.drop_table("assets")
