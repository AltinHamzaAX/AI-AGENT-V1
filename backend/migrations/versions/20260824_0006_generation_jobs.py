"""Add durable post generation jobs.

Revision ID: 20260824_0006
Revises: 20260824_0005
Create Date: 2026-08-24
"""

from collections.abc import Sequence
from hashlib import sha256
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0006"
down_revision: str | Sequence[str] | None = "20260824_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_generation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("last_error_code", sa.String(length=200), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_post_generation_jobs_non_negative_attempts"),
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name=op.f("ck_post_generation_jobs_positive_max_attempts"),
        ),
        sa.CheckConstraint(
            "timeout_seconds > 0",
            name=op.f("ck_post_generation_jobs_positive_timeout_seconds"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_scheduled', 'completed', "
            "'failed', 'dead')",
            name=op.f("ck_post_generation_jobs_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["post_generations.id"],
            name=op.f("fk_post_generation_jobs_generation_id_post_generations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_generation_jobs")),
        sa.UniqueConstraint(
            "generation_id",
            name=op.f("uq_post_generation_jobs_generation"),
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_post_generation_jobs_idempotency"),
        ),
    )
    op.create_index(
        "ix_post_generation_jobs_claim",
        "post_generation_jobs",
        ["status", "available_at", "leased_until"],
    )

    connection = op.get_bind()
    generations = connection.execute(
        sa.text("SELECT id, status FROM post_generations")
    ).mappings()
    job_table = sa.table(
        "post_generation_jobs",
        sa.column("id", sa.Uuid()),
        sa.column("generation_id", sa.Uuid()),
        sa.column("idempotency_key", sa.String()),
        sa.column("status", sa.String()),
        sa.column("attempts", sa.Integer()),
        sa.column("max_attempts", sa.Integer()),
        sa.column("timeout_seconds", sa.Integer()),
    )
    rows = []
    for generation in generations:
        generation_id = generation["id"]
        generation_status = generation["status"]
        job_status = "completed" if generation_status == "completed" else "queued"
        if generation_status in {"failed", "cancelled"}:
            job_status = "failed"
        rows.append(
            {
                "id": uuid4(),
                "generation_id": generation_id,
                "idempotency_key": sha256(f"backfill:{generation_id}".encode()).hexdigest(),
                "status": job_status,
                "attempts": 0,
                "max_attempts": 3,
                "timeout_seconds": 900,
            }
        )
    if rows:
        connection.execute(job_table.insert(), rows)
    connection.execute(
        sa.text("UPDATE post_generations SET status = 'queued' WHERE status = 'pending'")
    )


def downgrade() -> None:
    op.drop_index("ix_post_generation_jobs_claim", table_name="post_generation_jobs")
    op.drop_table("post_generation_jobs")
