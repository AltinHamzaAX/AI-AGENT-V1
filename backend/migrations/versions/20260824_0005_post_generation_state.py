"""Add persisted and versioned Post generation workflow state.

Revision ID: 20260824_0005
Revises: 20260824_0004
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0005"
down_revision: str | Sequence[str] | None = "20260824_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INITIAL_STATE = {
    "conversation_context": {},
    "brief": {},
    "semantic_contract": {},
    "brand": {},
    "product": {},
    "assets": [],
    "audience": {},
    "research": {},
    "marketing_strategy": {},
    "creative_concept": {},
    "copy": {},
    "art_direction": {},
    "design_spec": {},
    "generation_plan": {},
    "generation_artifacts": [],
    "quality": {},
    "revision_history": [],
}


def upgrade() -> None:
    op.create_table(
        "post_generation_states",
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint(
            "schema_version > 0",
            name=op.f("ck_post_generation_states_positive_schema_version"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_post_generation_states_positive_version"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["post_generations.id"],
            name=op.f("fk_post_generation_states_generation_id_post_generations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("generation_id", name=op.f("pk_post_generation_states")),
    )

    op.create_table(
        "post_generation_state_versions",
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("changed_section", sa.String(length=50), nullable=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name=op.f("ck_post_generation_state_versions_positive_schema_version"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_post_generation_state_versions_positive_version"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["post_generations.id"],
            name=op.f("fk_post_generation_state_versions_generation_id_post_generations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "generation_id",
            "version",
            name=op.f("pk_post_generation_state_versions"),
        ),
    )
    op.create_index(
        "ix_post_generation_state_versions_generation",
        "post_generation_state_versions",
        ["generation_id", "version"],
    )

    connection = op.get_bind()
    generation_ids = connection.execute(sa.text("SELECT id FROM post_generations")).scalars()
    state_table = sa.table(
        "post_generation_states",
        sa.column("generation_id", sa.Uuid()),
        sa.column("schema_version", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("state", postgresql.JSONB()),
    )
    version_table = sa.table(
        "post_generation_state_versions",
        sa.column("generation_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("schema_version", sa.Integer()),
        sa.column("changed_section", sa.String()),
        sa.column("state", postgresql.JSONB()),
    )
    rows = [
        {
            "generation_id": generation_id,
            "schema_version": 1,
            "version": 1,
            "state": INITIAL_STATE,
        }
        for generation_id in generation_ids
    ]
    if rows:
        connection.execute(state_table.insert(), rows)
        connection.execute(
            version_table.insert(),
            [{**row, "changed_section": None} for row in rows],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_post_generation_state_versions_generation",
        table_name="post_generation_state_versions",
    )
    op.drop_table("post_generation_state_versions")
    op.drop_table("post_generation_states")
