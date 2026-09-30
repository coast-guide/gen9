"""outcomes: a task's rubric and tries, and each run's evaluation

Revision ID: b6e2c8f4d1a9
Revises: a3d9f1c7e2b8

A task may say what done looks like (a Markdown rubric) and how many runs a firing may take to meet
it; a grader's verdict on each run is kept (outcomes.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b6e2c8f4d1a9"
down_revision: str | Sequence[str] | None = "a3d9f1c7e2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("rubric", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("max_iterations", sa.Integer(), server_default="3", nullable=False),
    )
    op.create_table(
        "outcome_evaluations",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column(
            "result",
            sa.String(24),
            nullable=False,
            comment="satisfied, needs_revision or failed",
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("criteria", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # A graded firing that fell short of its rubric sends its own kind of notice
    op.alter_column(
        "run_notices",
        "kind",
        existing_type=sa.String(16),
        comment="done, waiting, failed or unmet",
        existing_comment="done, waiting or failed",
    )


def downgrade() -> None:
    op.alter_column(
        "run_notices",
        "kind",
        existing_type=sa.String(16),
        comment="done, waiting or failed",
        existing_comment="done, waiting, failed or unmet",
    )
    op.drop_table("outcome_evaluations")
    op.drop_column("tasks", "max_iterations")
    op.drop_column("tasks", "rubric")
