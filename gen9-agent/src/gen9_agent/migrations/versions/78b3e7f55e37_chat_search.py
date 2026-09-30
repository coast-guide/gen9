"""chat_search: past chats, searchable (BM25, vector, trigram titles)

Revision ID: 78b3e7f55e37
Revises: 1cefdf9bd55d

The BM25 index needs pg_textsearch, which gen9-postgres creates as the superuser
(gen9-postgres/scripts/ensure-extensions.sh), with `vector`. pg_trgm is a trusted extension, so
the database owner creates it here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "78b3e7f55e37"
down_revision: str | Sequence[str] | None = "1cefdf9bd55d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "chat_search",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("embed_model", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_chat_search_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["threads.id"],
            name=op.f("fk_chat_search_thread_id_threads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_chat_search_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_chat_search")),
    )
    op.create_index(op.f("ix_chat_search_thread_id"), "chat_search", ["thread_id"])
    op.create_index(op.f("ix_chat_search_user_id"), "chat_search", ["user_id"])
    # BM25 over the question and answer (queries name it: to_bm25query($1, 'chat_search_bm25'))
    op.execute(
        "CREATE INDEX chat_search_bm25 ON chat_search USING bm25 (body) WITH (text_config = 'english')"
    )
    # Nearest neighbours by cosine distance
    op.execute(
        "CREATE INDEX chat_search_hnsw ON chat_search USING hnsw (embedding vector_cosine_ops)"
    )
    # Chats found by a misspelled title
    op.execute(
        "CREATE INDEX threads_title_trgm ON threads USING gin (title gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS threads_title_trgm")
    op.drop_table("chat_search")
