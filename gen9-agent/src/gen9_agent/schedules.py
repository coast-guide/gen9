"""The Temporal Schedules Gen9 keeps, created or updated by the worker at startup so they follow
the settings (docs/temporal.md, "Where Gen9 uses Temporal")."""

import logging
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import (
    Priority,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.service import RPCError, RPCStatusCode

from .temporal import GEN9_KIND
from .workflows.deletion import SweepDeletedUsersWorkflow
from .workflows.directory import SyncDirectoryWorkflow
from .workflows.names import (
    DIRECTORY_SCHEDULE_ID,
    PLUGINS_SCHEDULE_ID,
    PRIORITY_MAINTENANCE,
    REINDEX_SCHEDULE_ID,
    SWEEP_SCHEDULE_ID,
    SYSTEM_QUEUE,
)
from .workflows.plugins import SyncPluginSourcesWorkflow
from .workflows.search import ReindexInput, ReindexSearchWorkflow

log = logging.getLogger(__name__)


async def _ensure_interval_schedule(
    client: Client,
    schedule_id: str,
    action: ScheduleActionStartWorkflow,
    every_s: int,
    note: str,
) -> None:
    """Every `every_s` seconds, `action`; a run still going when the next one is due makes that one
    skip. Created, or updated to match; 0 removes the Schedule."""
    handle = client.get_schedule_handle(schedule_id)
    if every_s <= 0:
        try:
            await handle.delete()
            log.info("schedule %s removed", schedule_id)
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise
        return
    schedule = Schedule(
        action=action,
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=every_s))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        state=ScheduleState(note=note),
    )
    try:
        await client.create_schedule(schedule_id, schedule)
        log.info("schedule %s created: every %d s", schedule_id, every_s)
    except ScheduleAlreadyRunningError:

        def update(_: ScheduleUpdateInput) -> ScheduleUpdate:
            return ScheduleUpdate(schedule=schedule)

        await handle.update(update)
        log.info("schedule %s updated: every %d s", schedule_id, every_s)


async def ensure_sweep_schedule(client: Client, every_s: int) -> None:
    """SweepDeletedUsersWorkflow every `every_s` seconds (0: none)."""
    await _ensure_interval_schedule(
        client,
        SWEEP_SCHEDULE_ID,
        ScheduleActionStartWorkflow(
            SweepDeletedUsersWorkflow.run,
            id=SWEEP_SCHEDULE_ID,  # the Schedule appends the time of each run
            task_queue=SYSTEM_QUEUE,
            typed_search_attributes=TypedSearchAttributes(
                [SearchAttributePair(GEN9_KIND, "sweep")]
            ),
            priority=Priority(priority_key=PRIORITY_MAINTENANCE),
        ),
        every_s,
        "Removes Gen9's data of users deleted in Keycloak (kept by gen9-agent-worker)",
    )


async def ensure_reindex_schedule(client: Client, every_s: int) -> None:
    """ReindexSearchWorkflow every `every_s` seconds (0: none)."""
    await _ensure_interval_schedule(
        client,
        REINDEX_SCHEDULE_ID,
        ScheduleActionStartWorkflow(
            ReindexSearchWorkflow.run,
            ReindexInput(),
            id=REINDEX_SCHEDULE_ID,
            task_queue=SYSTEM_QUEUE,
            typed_search_attributes=TypedSearchAttributes(
                [SearchAttributePair(GEN9_KIND, "reindex")]
            ),
            priority=Priority(priority_key=PRIORITY_MAINTENANCE),
        ),
        every_s,
        "Makes older chats searchable by meaning, and again after `embed` changes "
        "(kept by gen9-agent-worker)",
    )


async def ensure_directory_schedule(client: Client, every_s: int) -> None:
    """SyncDirectoryWorkflow every `every_s` seconds (0: none): the connector directory's copy of
    the MCP registry, which asks its readers to sync infrequently, about hourly."""
    await _ensure_interval_schedule(
        client,
        DIRECTORY_SCHEDULE_ID,
        ScheduleActionStartWorkflow(
            SyncDirectoryWorkflow.run,
            id=DIRECTORY_SCHEDULE_ID,
            task_queue=SYSTEM_QUEUE,
            typed_search_attributes=TypedSearchAttributes(
                [SearchAttributePair(GEN9_KIND, "directory")]
            ),
            priority=Priority(priority_key=PRIORITY_MAINTENANCE),
        ),
        every_s,
        "Keeps the connector directory's copy of the MCP registry (kept by gen9-agent-worker)",
    )


async def ensure_plugins_schedule(client: Client, every_s: int) -> None:
    """SyncPluginSourcesWorkflow every `every_s` seconds (0: none): every plugin source admins
    added, fetched again (plugin_sources.py)."""
    await _ensure_interval_schedule(
        client,
        PLUGINS_SCHEDULE_ID,
        ScheduleActionStartWorkflow(
            SyncPluginSourcesWorkflow.run,
            id=PLUGINS_SCHEDULE_ID,
            task_queue=SYSTEM_QUEUE,
            typed_search_attributes=TypedSearchAttributes(
                [SearchAttributePair(GEN9_KIND, "plugins")]
            ),
            priority=Priority(priority_key=PRIORITY_MAINTENANCE),
        ),
        every_s,
        "Syncs the plugin sources admins added (kept by gen9-agent-worker)",
    )
