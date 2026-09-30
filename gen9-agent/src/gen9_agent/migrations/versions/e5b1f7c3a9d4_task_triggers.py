"""task triggers: a task's API token, kept as a hash

Revision ID: e5b1f7c3a9d4
Revises: d7a3c9e1f5b2

A task can be fired over HTTP with a token of its own (api/tasks.py). Only its SHA-256 is kept;
the token is shown once, and a new one replaces it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b1f7c3a9d4"
down_revision: str | Sequence[str] | None = "d7a3c9e1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("trigger_hash", sa.String(64), nullable=True))
    op.add_column(
        "tasks", sa.Column("trigger_made_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tasks", "trigger_made_at")
    op.drop_column("tasks", "trigger_hash")
