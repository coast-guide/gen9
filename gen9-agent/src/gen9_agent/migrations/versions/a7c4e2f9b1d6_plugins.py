"""plugins: marketplaces admins add from git, their plugins, and their skills' files

Revision ID: a7c4e2f9b1d6
Revises: d5f2b8c4a1e7

A plugin source is a git repository with a marketplace (Codex's or Claude Code's format), synced by
the worker (plugin_sources.py). Each plugin it lists is loaded (plugins.py) and kept with its load
report and the files of its skills. Plugins start "off": an admin makes them available.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a7c4e2f9b1d6"
down_revision: str | Sequence[str] | None = "d5f2b8c4a1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_sources",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), primary_key=True
        ),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("ref", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("format", sa.String(16), nullable=True, comment="codex or claude"),
        sa.Column("commit", sa.String(40), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            server_default="pending",
            nullable=False,
            comment="pending, synced or failed",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "added_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "plugins",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), primary_key=True
        ),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("plugin_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("format", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "availability",
            sa.String(16),
            server_default="off",
            nullable=False,
            comment="off, available or installed",
        ),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column("commit", sa.String(40), nullable=True),
        sa.Column("report", JSONB(), server_default="{}", nullable=False),
        sa.Column("digest", sa.String(64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source_id", "name", name="uq_plugins_source_name"),
    )
    op.create_table(
        "plugin_files",
        sa.Column(
            "plugin_id",
            sa.Uuid(),
            sa.ForeignKey("plugins.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plugin_files")
    op.drop_table("plugins")
    op.drop_table("plugin_sources")
