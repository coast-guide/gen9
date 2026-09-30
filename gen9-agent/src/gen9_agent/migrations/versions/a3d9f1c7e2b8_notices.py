"""notices: emails about background runs, and who wants them

Revision ID: a3d9f1c7e2b8
Revises: f2c8d4a6b9e1

A person chooses which emails they get about their background runs (users.notify), and each run
sends each kind of notice once (run_notices; notices.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3d9f1c7e2b8"
down_revision: str | Sequence[str] | None = "f2c8d4a6b9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "notify",
            sa.String(16),
            server_default="all",
            nullable=False,
            comment="all, needs_you or never",
        ),
    )
    op.create_table(
        "run_notices",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "kind", sa.String(16), primary_key=True, comment="done, waiting or failed"
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("run_notices")
    op.drop_column("users", "notify")
