"""search past chats: whether a chat's agent may search the person's past chats

Revision ID: d3a7e1c9f4b6
Revises: c1f4a8e2d7b3

A person may turn off the agent's search of their past chats (past_chats.py), as Claude's
"Search and reference chats".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a7e1c9f4b6"
down_revision: str | Sequence[str] | None = "c1f4a8e2d7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "search_past_chats", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "search_past_chats")
