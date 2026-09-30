"""threads.permission_mode: Ask before acting, or Act, ask when unsure

Revision ID: d8f2b6a1c9e4
Revises: c4e1a9d2b7f3

Each chat keeps the permission mode its last message was sent with (gen9_agent/approvals.py);
existing chats get the default, "auto".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f2b6a1c9e4"
down_revision: str | Sequence[str] | None = "c4e1a9d2b7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "permission_mode",
            sa.String(length=8),
            server_default="auto",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("threads", "permission_mode")
