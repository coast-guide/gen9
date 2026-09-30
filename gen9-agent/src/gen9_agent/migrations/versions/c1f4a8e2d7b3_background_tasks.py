"""background tasks: a chat started by another chat's agent

Revision ID: c1f4a8e2d7b3
Revises: b6e2c8f4d1a9

A chat's agent may start work in the background, as a chat of its own (background.py); it keeps
the chat that started it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f4a8e2d7b3"
down_revision: str | Sequence[str] | None = "b6e2c8f4d1a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey("threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_threads_parent_id", "threads", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_parent_id", table_name="threads")
    op.drop_column("threads", "parent_id")
