"""connectors: remote MCP servers people connect

Revision ID: e3a7c5d91b20
Revises: d8f2b6a1c9e4

A person's connectors (gen9_agent/connectors.py), with a sealed token (vault.py) and an approval
policy. They go with the person (ON DELETE CASCADE).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3a7c5d91b20"
down_revision: str | Sequence[str] | None = "d8f2b6a1c9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("header", sa.String(length=64), nullable=True),
        sa.Column("sealed_token", sa.Text(), nullable=True),
        sa.Column(
            "policy",
            sa.String(length=16),
            server_default="ask",
            nullable=False,
            comment="ask, changes or never",
        ),
        sa.Column(
            "tools",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_connectors_user_name"),
    )
    op.create_index("ix_connectors_user_id", "connectors", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_connectors_user_id", table_name="connectors")
    op.drop_table("connectors")
