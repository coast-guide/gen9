"""chat_search: embeddings of any model, one HNSW index per model

Revision ID: e75adb853568
Revises: 78b3e7f55e37

`embedding` becomes an untyped `vector`, so changing the router's `embed` alias needs no
migration. The single HNSW index on vector(1536) becomes one partial expression index per model
(gen9_agent/search_index.py; explore/search/NOTES.md, "Re-embedding when `embed` changes"). Rows
already embedded keep their index, under their model's name.
"""

import hashlib
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "e75adb853568"
down_revision: str | Sequence[str] | None = "78b3e7f55e37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_name(model: str) -> str:
    # As gen9_agent/search_index.py; kept here so this migration never changes
    return "chat_search_hnsw_" + hashlib.sha256(model.encode()).hexdigest()[:12]


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chat_search_hnsw")
    op.execute("ALTER TABLE chat_search ALTER COLUMN embedding TYPE vector")
    models = op.get_bind().execute(
        text(
            "select embed_model, min(vector_dims(embedding)) from chat_search"
            " where embedding is not null and embed_model is not null group by embed_model"
        )
    )
    for model, dims in models:
        if dims > 2000:
            continue  # the reindex workflow indexes it as halfvec (search_index.py)
        literal = "'" + model.replace("'", "''") + "'"
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_index_name(model)} ON chat_search"
            f" USING hnsw ((embedding::vector({dims})) vector_cosine_ops)"
            f" WHERE embed_model = {literal}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for (name,) in bind.execute(
        text(
            "select indexname from pg_indexes"
            " where tablename = 'chat_search' and indexname like 'chat_search_hnsw_%'"
        )
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    # Embeddings of another size can't stay in vector(1536); the reindex workflow makes them again
    op.execute(
        "UPDATE chat_search SET embedding = NULL, embed_model = NULL"
        " WHERE vector_dims(embedding) <> 1536"
    )
    op.execute("ALTER TABLE chat_search ALTER COLUMN embedding TYPE vector(1536)")
    op.execute(
        "CREATE INDEX chat_search_hnsw ON chat_search USING hnsw (embedding vector_cosine_ops)"
    )
