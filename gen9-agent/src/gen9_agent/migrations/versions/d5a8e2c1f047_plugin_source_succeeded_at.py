"""When a plugin source last synced successfully: `synced_at` is set by every sync, failed ones
too ("Sync now" waits for it to change), so a failed source couldn't say how old the plugins it
still offers are (docs/plans/manual-e2e.md, P3-D5). A source synced now takes its sync time; a
failed one's is unknown.

Revision ID: d5a8e2c1f047
Revises: b7e2c4f9a1d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a8e2c1f047"
down_revision: str | None = "b7e2c4f9a1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plugin_sources",
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE plugin_sources SET succeeded_at = synced_at WHERE status = 'synced'"
    )


def downgrade() -> None:
    op.drop_column("plugin_sources", "succeeded_at")
