"""environment secrets: a person's secrets for hosts, added to their environments' requests

Revision ID: c3e8a1f6d2b4
Revises: b7d2f4a9c3e1

OpenSandbox's credential vault adds a secret to requests from a chat's environment to its host,
so code there never sees it (environments.py). The value is sealed (vault.py) and deleted with its
owner.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a1f6d2b4"
down_revision: str | Sequence[str] | None = "b7d2f4a9c3e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environment_secrets",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), primary_key=True
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("path", sa.String(256), server_default="/*", nullable=False),
        sa.Column(
            "auth", sa.String(16), nullable=False, comment="bearer, header or basic"
        ),
        sa.Column("header", sa.String(64), nullable=True),
        sa.Column("sealed_value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_environment_secrets_user_name"),
    )
    op.create_index(
        "ix_environment_secrets_user_id", "environment_secrets", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_environment_secrets_user_id", table_name="environment_secrets")
    op.drop_table("environment_secrets")
