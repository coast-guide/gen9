"""The Activities of ReindexSearchWorkflow (`workflows/search.py`). Calls to the router here are
made on behalf of no user: re-embedding follows the operator's change of model, and backfilling
follows an upgrade, so neither counts against anyone's budget."""

import logging
import uuid

import httpx
from sqlalchemy import text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .model_router import embed
from .runs.indexing import index_run
from .runtime import Runtime
from .search_index import check_model, drop_stale_indexes, ensure_index
from .temporal import heartbeating
from .workflows.names import (
    CURRENT_EMBED_MODEL,
    DROP_STALE_SEARCH_INDEXES,
    ENSURE_SEARCH_INDEX,
    REINDEX_BATCH,
)
from .workflows.search import BatchResult, EmbedModel, ReindexBatch

log = logging.getLogger(__name__)

# Successful runs of chats not being deleted whose row is missing, has no embedding, or has
# another model's; after the cursor, in order of id
_NEEDING_WORK = text(
    """
    select r.id from runs r
    join threads t on t.id = r.thread_id
    left join chat_search s on s.run_id = r.id
    where r.status = 'success' and t.deleted_at is null
      and (s.run_id is null or s.embedding is null or s.embed_model is distinct from :model)
      and (cast(:after as uuid) is null or r.id > cast(:after as uuid))
    order by r.id
    limit :limit
    """
)


class SearchActivities:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    @activity.defn(name=CURRENT_EMBED_MODEL)
    async def current_embed_model(self) -> EmbedModel:
        """What `embed` is now: the router names the model that answered, and the vector's
        length is its dimension."""
        [vector], model = await embed(
            self.runtime.settings, self.runtime.models, ["gen9"]
        )
        if not model:
            raise ApplicationError(
                "the router named no embedding model", non_retryable=True
            )
        return EmbedModel(check_model(model), len(vector))

    @activity.defn(name=ENSURE_SEARCH_INDEX)
    async def ensure_search_index(self, current: EmbedModel) -> str | None:
        async with heartbeating():
            return await ensure_index(
                self.runtime.engine,
                current.model,
                current.dims,
                self.runtime.settings.search_index_memory,
            )

    @activity.defn(name=REINDEX_BATCH)
    async def reindex_batch(self, batch: ReindexBatch) -> BatchResult:
        async with self.runtime.engine.connect() as conn:
            run_ids: list[uuid.UUID] = list(
                (
                    await conn.scalars(
                        _NEEDING_WORK,
                        {
                            "model": batch.model,
                            "after": batch.after,
                            "limit": batch.limit,
                        },
                    )
                ).all()
            )
        embedded = failed = 0
        async with heartbeating():
            for run_id in run_ids:
                try:
                    embedded += await index_run(self.runtime, run_id, None)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500:
                        raise  # the router or its provider is down: retry the batch
                    # This chat's text was refused (4xx): skip it, the cursor moves past it
                    log.warning(
                        "run %s not embedded: HTTP %d", run_id, e.response.status_code
                    )
                    failed += 1
                except ApplicationError as e:
                    log.warning("run %s not embedded: %s", run_id, e)
                    failed += 1
        last = str(run_ids[-1]) if len(run_ids) == batch.limit else None
        log.info(
            "reindex batch: %d embedded, %d failed%s",
            embedded,
            failed,
            "" if last else ", none left",
        )
        return BatchResult(embedded, failed, last)

    @activity.defn(name=DROP_STALE_SEARCH_INDEXES)
    async def drop_stale_search_indexes(self, model: str) -> list[str]:
        return await drop_stale_indexes(self.runtime.engine, model)

    def all(self) -> list:
        return [
            self.current_embed_model,
            self.ensure_search_index,
            self.reindex_batch,
            self.drop_stale_search_indexes,
        ]
