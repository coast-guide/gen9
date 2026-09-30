"""plugin installs: the plugins each person added

Revision ID: b9e3d7a2c5f1
Revises: a7c4e2f9b1d6

A person adds a plugin an admin made available, and its skills join their chats
(plugin_skills.py). Goes with the person or the plugin.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e3d7a2c5f1"
down_revision: str | Sequence[str] | None = "a7c4e2f9b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_installs",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "plugin_id",
            sa.Uuid(),
            sa.ForeignKey("plugins.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("plugin_installs")
