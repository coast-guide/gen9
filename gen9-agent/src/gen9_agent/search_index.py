"""The vector indexes of `chat_search`, one per embedding model (explore/search/NOTES.md,
"Re-embedding when `embed` changes").

`chat_search.embedding` is an untyped `vector`, so any model's embeddings fit, and `embed` can
change without a migration. HNSW needs one dimension per index, so each model gets a partial
expression index over its own rows, and search casts the same way (pgvector's README, "Can I store
vectors with different dimensions in the same column?"). HNSW indexes `vector` up to 2,000
dimensions and `halfvec` up to 4,000; beyond that a model's rows are searched exactly.

The services' role owns no table, so the indexes are built and dropped by two functions the owner
defines (migration c3e7a1f9d2b8), which check their inputs again and name and shape each index
as `index_name` and `vector_type` do here.
"""

import hashlib
import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

INDEX_PREFIX = "chat_search_hnsw_"
# What the router names a model (x-litellm-model-name): inlined in index DDL, which takes no
# bound parameters, so anything else is refused
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")
MAX_VECTOR_DIMS = 2_000
MAX_HALFVEC_DIMS = 4_000


def index_name(model: str) -> str:
    return INDEX_PREFIX + hashlib.sha256(model.encode()).hexdigest()[:12]


def check_model(model: str) -> str:
    if not _MODEL_NAME.fullmatch(model):
        raise ValueError(f"unexpected embedding model name: {model!r}")
    return model


def vector_type(dims: int) -> str | None:
    """The type a model's rows are cast to, in its index and in search (None: no index)."""
    if not 1 <= dims <= 16_000:
        raise ValueError(f"unexpected embedding dimensions: {dims}")
    if dims <= MAX_VECTOR_DIMS:
        return f"vector({dims})"
    if dims <= MAX_HALFVEC_DIMS:
        return f"halfvec({dims})"
    return None


def distance_sql(dims: int) -> str:
    """Cosine distance between a row and the query (`:vector`), in the form the model's index
    serves."""
    cast = vector_type(dims) or f"vector({dims})"
    return f"(s.embedding::{cast} <=> cast(:vector as {cast}))"


async def ensure_index(
    engine: AsyncEngine, model: str, dims: int, memory: str
) -> str | None:
    """The model's HNSW index, built if missing and rebuilt if a failed build left it invalid, with
    `maintenance_work_mem` raised for the build (a build that outgrows it was 3.7 times slower,
    NOTES.md). The worker builds it before any row uses the model, so it is quick. Returns its
    name, or None when the dimensions are past what HNSW indexes."""
    check_model(model)
    if vector_type(dims) is None:
        log.warning("%s has %d dimensions: searched without an index", model, dims)
        return None
    async with engine.begin() as conn:
        name = await conn.scalar(
            text("select search_index_ensure(:model, :dims, :memory)"),
            {"model": model, "dims": dims, "memory": memory},
        )
    log.info("index %s: %s, %s", name, model, vector_type(dims))
    return name


async def drop_stale_indexes(engine: AsyncEngine, model: str) -> list[str]:
    """Drops the other models' indexes once every row has this model's embedding; while any row
    still has another model's, keeps them all."""
    check_model(model)
    async with engine.begin() as conn:
        others = await conn.scalar(
            text(
                "select count(*) from chat_search"
                " where embedding is not null and embed_model is distinct from :model"
            ),
            {"model": model},
        )
        if others:
            log.info(
                "%d rows still have another model's embedding: indexes kept", others
            )
            return []
        dropped = list(
            (
                await conn.scalars(
                    text("select search_index_drop_stale(:model)"), {"model": model}
                )
            ).all()
        )
    for name in dropped:
        log.info("index %s dropped: no row uses its model", name)
    return dropped
