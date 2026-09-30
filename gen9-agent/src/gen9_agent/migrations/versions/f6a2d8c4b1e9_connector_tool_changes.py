"""A connector's tools the server added or changed since the person kept them (a "rug pull"):
held back from the agent until they look (connectors.py, `changes`; docs/plans/manual-e2e.md,
P5-C4). What they kept is `tools`, each with its `pin` from now on. Connectors kept before have
no pins: their tools wait for one look.

Revision ID: f6a2d8c4b1e9
Revises: e3b7c1d9a5f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f6a2d8c4b1e9"
down_revision: str | None = "e3b7c1d9a5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("connectors", sa.Column("changed", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("connectors", "changed")
