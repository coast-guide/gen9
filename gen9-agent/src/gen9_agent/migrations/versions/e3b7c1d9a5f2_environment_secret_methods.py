"""Which requests an environment secret goes with: `read` (GET, HEAD, OPTIONS) or `all` (changes
too). In a chat that acts without asking, a command could send anything to a secret's host as the
person, so a new secret reads only unless they choose otherwise (docs/plans/manual-e2e.md,
P5-C2). Secrets kept before keep what they were given: `all`.

Revision ID: e3b7c1d9a5f2
Revises: d5a8e2c1f047
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7c1d9a5f2"
down_revision: str | None = "d5a8e2c1f047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environment_secrets",
        sa.Column(
            "methods",
            sa.String(8),
            nullable=False,
            server_default="all",
            comment="read or all",
        ),
    )
    op.alter_column("environment_secrets", "methods", server_default="read")


def downgrade() -> None:
    op.drop_column("environment_secrets", "methods")
