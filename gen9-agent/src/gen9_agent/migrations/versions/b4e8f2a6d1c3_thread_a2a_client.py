"""The A2A client (its token's `azp`) that started a chat. A client reaches only the chats it
started, as its consent says ("send it tasks and read their results"), not the person's own chats
or another agent's (a2a_server.py; docs/plans/manual-e2e.md, P5-C7). Chats started before have
none: A2A clients no longer reach them, and the person still does.

Revision ID: b4e8f2a6d1c3
Revises: a9d3e7b2c5f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8f2a6d1c3"
down_revision: str | None = "a9d3e7b2c5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("a2a_client", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("threads", "a2a_client")
