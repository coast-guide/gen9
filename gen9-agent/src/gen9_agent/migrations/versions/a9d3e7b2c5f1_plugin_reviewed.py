"""What an admin agreed to when they chose who may have a plugin (`reviewed`,
plugin_skills.fingerprint: its kept files and its report, as one hash). A sync that changes an
available plugin no longer reaches people until an admin looks again (docs/plans/manual-e2e.md,
P5-C4). Plugins available now keep being available as they are.

Revision ID: a9d3e7b2c5f1
Revises: f6a2d8c4b1e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3e7b2c5f1"
down_revision: str | None = "f6a2d8c4b1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plugins", sa.Column("reviewed", sa.String(64), nullable=True))
    # plugin_skills.fingerprint, in SQL
    op.execute(
        "UPDATE plugins SET reviewed = encode(sha256(convert_to("
        "concat(coalesce(digest, ''), CAST(report AS TEXT)), 'UTF8')), 'hex') "
        "WHERE availability <> 'off' AND status = 'loaded'"
    )


def downgrade() -> None:
    op.drop_column("plugins", "reviewed")
