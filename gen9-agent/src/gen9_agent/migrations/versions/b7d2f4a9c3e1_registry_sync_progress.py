"""registry sync progress: a pass under way resumes from Postgres

Revision ID: b7d2f4a9c3e1
Revises: a6c1e8f3b5d2

A first pass over the official MCP Registry takes hours (a page in 20 s to 2 min at times), and an
Activity's heartbeat details die with its workflow: after its retries ran out, the next hourly run
would start over. `cursor` and `pass_started` keep a pass under way in Postgres, written with each
page, so any later attempt or workflow resumes it. `synced_until` is empty until a first pass ends.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a9c3e1"
down_revision: str | Sequence[str] | None = "a6c1e8f3b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("registry_sync", "synced_until", nullable=True)
    op.add_column("registry_sync", sa.Column("cursor", sa.Text(), nullable=True))
    op.add_column(
        "registry_sync",
        sa.Column("pass_started", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("registry_sync", "pass_started")
    op.drop_column("registry_sync", "cursor")
    op.execute("DELETE FROM registry_sync WHERE synced_until IS NULL")
    op.alter_column("registry_sync", "synced_until", nullable=False)
