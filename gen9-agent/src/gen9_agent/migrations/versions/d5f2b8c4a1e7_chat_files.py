"""chat files: files a chat's environment shared, and files the person attached

Revision ID: d5f2b8c4a1e7
Revises: c3e8a1f6d2b4

Gen9 keeps a chat's files so they outlive its environment (chat_files.py): what the agent saves in
/work/out, captured after each turn, and what the person attaches. Deleted with the chat.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f2b8c4a1e7"
down_revision: str | Sequence[str] | None = "c3e8a1f6d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_files",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), primary_key=True
        ),
        sa.Column(
            "thread_id",
            sa.Uuid(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("origin", sa.String(8), nullable=False, comment="output or upload"),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "thread_id", "origin", "path", name="uq_chat_files_thread_path"
        ),
    )
    op.create_index("ix_chat_files_thread_id", "chat_files", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_files_thread_id", table_name="chat_files")
    op.drop_table("chat_files")
