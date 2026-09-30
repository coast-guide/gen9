"""task fires: each fire outside a task's schedule, for the hourly limits

Revision ID: f2c8d4a6b9e1
Revises: e5b1f7c3a9d4

The API records a fire (a task's API trigger, or Run now) in the request that makes it, under the
task's and the person's row locks, so a burst of calls can't slip past the limits before the
worker makes their chats (api/tasks.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c8d4a6b9e1"
down_revision: str | Sequence[str] | None = "e5b1f7c3a9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_fires",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), primary_key=True
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_task_fires_task_at", "task_fires", ["task_id", "at"])


def downgrade() -> None:
    op.drop_table("task_fires")
