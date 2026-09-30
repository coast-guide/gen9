"""remember: whether Gen9 remembers things about a person

Revision ID: e8c2f5a1d9b7
Revises: d3a7e1c9f4b6

A person may turn memory off (memory.py), as Claude's memory can be paused.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8c2f5a1d9b7"
down_revision: str | Sequence[str] | None = "d3a7e1c9f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("remember", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "remember")
