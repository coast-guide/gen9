"""Syncing plugin sources (plugin_sources.py): one source at a time, from the admin API (when added,
or "Sync now"), and every one by the `sync-plugin-sources` Schedule, daily. A source that can't be
fetched is marked failed by the Activity, which returns: only an unexpected error is retried."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .names import LIST_PLUGIN_SOURCES, SYNC_PLUGIN_SOURCE, SYSTEM_QUEUE


@dataclass(frozen=True)
class PluginSyncResult:
    plugins: int
    loaded: int
    changed: int
    removed: int
    problems: list[str]


RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2,
    maximum_attempts=3,
)
# Fetching every plugin of a large marketplace, a few at a time
SYNC_TIMEOUT = timedelta(minutes=20)


@workflow.defn
class SyncPluginSourceWorkflow:
    @workflow.run
    async def run(self, source_id: str) -> PluginSyncResult:
        return await workflow.execute_activity(
            SYNC_PLUGIN_SOURCE,
            source_id,
            task_queue=SYSTEM_QUEUE,
            result_type=PluginSyncResult,
            start_to_close_timeout=SYNC_TIMEOUT,
            retry_policy=RETRY,
        )


@workflow.defn
class SyncPluginSourcesWorkflow:
    """Every source, one after another."""

    @workflow.run
    async def run(self) -> int:
        ids = await workflow.execute_activity(
            LIST_PLUGIN_SOURCES,
            task_queue=SYSTEM_QUEUE,
            result_type=list[str],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        for source_id in ids:
            await workflow.execute_activity(
                SYNC_PLUGIN_SOURCE,
                source_id,
                task_queue=SYSTEM_QUEUE,
                result_type=PluginSyncResult,
                start_to_close_timeout=SYNC_TIMEOUT,
                retry_policy=RETRY,
            )
        return len(ids)
