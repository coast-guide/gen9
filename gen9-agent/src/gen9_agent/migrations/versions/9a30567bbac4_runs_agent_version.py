"""runs.agent_version: which definition of the agent answered

Revision ID: 9a30567bbac4
Revises: e75adb853568

A hash of the agent's folder (gen9_agent/definition.py), set when a run's attempt starts. Runs
from before have none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a30567bbac4"
down_revision: str | Sequence[str] | None = "e75adb853568"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs", sa.Column("agent_version", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("runs", "agent_version")
