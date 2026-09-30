"""Keeping the connector directory's copy of the MCP registry (directory.py). One Activity does
the pass: it pages through the registry and saves each page with the pass's cursor in Postgres,
so a retry, or the next run, resumes where it stopped (the registry gives no uptime guarantees).
Started hourly by the `sync-directory` Schedule, and by the admin API."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .names import SYNC_DIRECTORY, SYSTEM_QUEUE


@dataclass(frozen=True)
class SyncResult:
    pages: int
    kept: int  # servers added or updated
    removed: int  # deleted in the registry, or no longer usable by a connector


# Each attempt resumes where Postgres says the last stopped, so many attempts lose nothing; the
# next hourly run resumes too if these run out
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=20,
)


@workflow.defn
class SyncDirectoryWorkflow:
    @workflow.run
    async def run(self) -> SyncResult:
        return await workflow.execute_activity(
            SYNC_DIRECTORY,
            task_queue=SYSTEM_QUEUE,
            result_type=SyncResult,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=3),
            retry_policy=RETRY,
        )
