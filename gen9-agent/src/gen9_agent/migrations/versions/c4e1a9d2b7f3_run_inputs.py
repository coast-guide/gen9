"""run_inputs, and runs that wait for the person

Revision ID: c4e1a9d2b7f3
Revises: 9a30567bbac4

A run can now pause for the person (a question mid-task): its status is `waiting`, which counts as
active, so the one-active-run-per-thread index includes it; `expired` ends a run nobody answered.
`run_inputs` keeps what was asked and what was answered (models.InputRequest).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e1a9d2b7f3"
down_revision: str | Sequence[str] | None = "9a30567bbac4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_inputs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, comment="question"),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "id"),
    )
    op.drop_index("uq_runs_one_active_per_thread", table_name="runs")
    op.create_index(
        "uq_runs_one_active_per_thread",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'waiting')"),
    )
    op.alter_column(
        "runs",
        "status",
        existing_type=sa.String(length=16),
        comment="queued, running, waiting, success, error, cancelled or expired",
        existing_comment="queued, running, success, error or cancelled",
    )


def downgrade() -> None:
    op.alter_column(
        "runs",
        "status",
        existing_type=sa.String(length=16),
        comment="queued, running, success, error or cancelled",
        existing_comment="queued, running, waiting, success, error, cancelled or expired",
    )
    op.drop_index("uq_runs_one_active_per_thread", table_name="runs")
    op.create_index(
        "uq_runs_one_active_per_thread",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("run_inputs")
