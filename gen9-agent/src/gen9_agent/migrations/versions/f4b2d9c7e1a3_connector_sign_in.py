"""connector sign-in: MCP authorization per person

Revision ID: f4b2d9c7e1a3
Revises: e3a7c5d91b20

A connector that needs sign-in (connector_auth.py) keeps what its authorization server said
(`sign_in`: resource, issuer, metadata, scope and Gen9's client id, none of it secret), and its
client secret and tokens sealed (vault.py). `status` says whether it's ready, waits for the
person to sign in, or needs them to sign in again. A sign-in under way is one row of
`connector_sign_ins`, keyed by the hash of its `state`. The row is used once, and ignored after
ten minutes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b2d9c7e1a3"
down_revision: str | Sequence[str] | None = "e3a7c5d91b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="ready",
            nullable=False,
            comment="ready, sign_in or reconnect",
        ),
    )
    op.add_column(
        "connectors",
        sa.Column("sign_in", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "connectors", sa.Column("sealed_client_secret", sa.Text(), nullable=True)
    )
    op.add_column("connectors", sa.Column("sealed_tokens", sa.Text(), nullable=True))
    op.create_table(
        "connector_sign_ins",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("sealed", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["connectors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index(
        "ix_connector_sign_ins_connector_id", "connector_sign_ins", ["connector_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_connector_sign_ins_connector_id", table_name="connector_sign_ins")
    op.drop_table("connector_sign_ins")
    op.drop_column("connectors", "sealed_tokens")
    op.drop_column("connectors", "sealed_client_secret")
    op.drop_column("connectors", "sign_in")
    op.drop_column("connectors", "status")
