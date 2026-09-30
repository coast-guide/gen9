"""plugin connectors: a plugin's remote MCP servers as the person's connectors

Revision ID: c4f8a2d6e9b3
Revises: b9e3d7a2c5f1

A connector a plugin brings (plugin_connectors.py) names its plugin and server; it goes with the
plugin, and a person has one per server of each plugin they have.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a2d6e9b3"
down_revision: str | Sequence[str] | None = "b9e3d7a2c5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column(
            "plugin_id",
            sa.Uuid(),
            sa.ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("connectors", sa.Column("plugin_server", sa.Text(), nullable=True))
    op.create_index("ix_connectors_plugin_id", "connectors", ["plugin_id"])
    op.create_unique_constraint(
        "uq_connectors_user_plugin",
        "connectors",
        ["user_id", "plugin_id", "plugin_server"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_connectors_user_plugin", "connectors", type_="unique")
    op.drop_index("ix_connectors_plugin_id", table_name="connectors")
    op.drop_column("connectors", "plugin_server")
    op.drop_column("connectors", "plugin_id")
