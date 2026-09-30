"""registry servers: Gen9's copy of an MCP registry for the connector directory

Revision ID: a6c1e8f3b5d2
Revises: f4b2d9c7e1a3

The servers a connector can use, from `MCP_REGISTRY_URL` (directory.py), kept by an hourly sync, as
the Registry asks of those that read it, and searched by name, title and description (pg_trgm).
`registry_sync` holds where the next sync starts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c1e8f3b5d2"
down_revision: str | Sequence[str] | None = "f4b2d9c7e1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registry_servers",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("header", sa.Text(), nullable=True),
        sa.Column("header_description", sa.Text(), nullable=True),
        sa.Column("repository_url", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            comment="active or deprecated",
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    # pg_trgm is created by the chat search migration (78b3e7f55e37)
    op.execute(
        "CREATE INDEX registry_servers_text_trgm ON registry_servers USING gin "
        "((lower(name || ' ' || coalesce(title, '') || ' ' || description)) gin_trgm_ops)"
    )
    op.create_table(
        "registry_sync",
        sa.Column("registry_url", sa.Text(), nullable=False),
        sa.Column("synced_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("registry_url"),
    )


def downgrade() -> None:
    op.drop_table("registry_sync")
    op.execute("DROP INDEX IF EXISTS registry_servers_text_trgm")
    op.drop_table("registry_servers")
