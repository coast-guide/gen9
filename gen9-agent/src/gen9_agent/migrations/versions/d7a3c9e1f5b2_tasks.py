"""tasks: what Gen9 runs on its own, on a schedule or once

Revision ID: d7a3c9e1f5b2
Revises: c4f8a2d6e9b3

A task is a saved message, when to send it and the permission mode its runs keep (tasks.py). Each
firing makes a chat, which names its task; the chats stay when the task goes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d7a3c9e1f5b2"
down_revision: str | Sequence[str] | None = "c4f8a2d6e9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), primary_key=True
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule", JSONB(), nullable=False),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column(
            "permission_mode", sa.String(8), server_default="auto", nullable=False
        ),
        sa.Column(
            "status",
            sa.String(8),
            server_default="active",
            nullable=False,
            comment="active, paused or done",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "threads",
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_threads_task_id", "threads", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_task_id", table_name="threads")
    op.drop_column("threads", "task_id")
    op.drop_table("tasks")
