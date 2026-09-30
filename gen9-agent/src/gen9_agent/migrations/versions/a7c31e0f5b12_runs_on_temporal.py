"""runs on Temporal: drop the Postgres queue's leases

Temporal executes runs now (workflows/runs.py): the claim index, leases and cancel flag of the
Postgres queue are gone. `attempts` stays, set by each Activity attempt.

Revision ID: a7c31e0f5b12
Revises: de192f1de2dd

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c31e0f5b12"
down_revision: str | Sequence[str] | None = "de192f1de2dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_runs_claimable",
        table_name="runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_column("runs", "lease_expires_at")
    op.drop_column("runs", "lease_owner")
    op.drop_column("runs", "cancel_requested")


def downgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.add_column(
        "runs", sa.Column("lease_owner", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_runs_claimable",
        "runs",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
