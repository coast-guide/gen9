"""Keeping past chats searchable by meaning (search_index.py; api/search.py).

`ReindexSearchWorkflow` makes every successful run's row carry the current `embed` model's
embedding: runs from before search have no row (backfill), and rows embedded by an earlier model
are embedded again. It works through runs in order of id, 100 at a time, and continues-as-new when
Temporal suggests it, carrying its place. Started by the `reindex-search` Schedule and the admin
API. Safe to repeat and to run twice at once: each row is rewritten with the same result.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .names import (
    CURRENT_EMBED_MODEL,
    DROP_STALE_SEARCH_INDEXES,
    ENSURE_SEARCH_INDEX,
    REINDEX_BATCH,
    SYSTEM_QUEUE,
)

BATCH_SIZE = 100
# The router may be down for a while; the next Schedule run picks up where nothing was done
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=10,
)


@dataclass(frozen=True)
class EmbedModel:
    model: str  # as the router names it (x-litellm-model-name)
    dims: int


@dataclass(frozen=True)
class ReindexBatch:
    model: str
    after: str | None  # the last run id already handled
    limit: int = BATCH_SIZE


@dataclass(frozen=True)
class BatchResult:
    embedded: int
    failed: int
    last: str | None  # None once no runs are left


@dataclass(frozen=True)
class ReindexInput:
    after: str | None = None
    embedded: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ReindexResult:
    model: str
    embedded: int
    failed: int
    dropped: list[str] = field(default_factory=list)


@workflow.defn
class ReindexSearchWorkflow:
    @workflow.run
    async def run(self, state: ReindexInput) -> ReindexResult:
        current = await workflow.execute_activity(
            CURRENT_EMBED_MODEL,
            task_queue=SYSTEM_QUEUE,
            result_type=EmbedModel,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        await workflow.execute_activity(
            ENSURE_SEARCH_INDEX,
            current,
            task_queue=SYSTEM_QUEUE,
            # A build over many rows takes minutes; the Activity heartbeats meanwhile
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        after, embedded, failed = state.after, state.embedded, state.failed
        while True:
            batch = await workflow.execute_activity(
                REINDEX_BATCH,
                ReindexBatch(current.model, after),
                task_queue=SYSTEM_QUEUE,
                result_type=BatchResult,
                start_to_close_timeout=timedelta(minutes=15),
                heartbeat_timeout=timedelta(minutes=1),
                retry_policy=RETRY,
            )
            embedded += batch.embedded
            failed += batch.failed
            if batch.last is None:
                break
            after = batch.last
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(ReindexInput(after, embedded, failed))
        dropped = await workflow.execute_activity(
            DROP_STALE_SEARCH_INDEXES,
            current.model,
            task_queue=SYSTEM_QUEUE,
            result_type=list[str],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RETRY,
        )
        return ReindexResult(current.model, embedded, failed, dropped)
