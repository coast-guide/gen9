"""`gen9-agent-worker`: a Temporal worker that executes Gen9's workflows and Activities.

One process runs two SDK workers:
- on `gen9-agent`, the agent turns, up to `WORKER_CONCURRENCY` at once;
- on `gen9-system`, the workflows (runs, deletions, the sweep) and their short Activities.
At start it also keeps the Schedule that sweeps users deleted in Keycloak in line with settings.

Run as many processes as you like (`docker compose up -d --scale worker=N`): Temporal spreads the
work, retries a turn whose worker died on another one, and delivers Stop to whichever worker runs
it. On SIGTERM a worker stops taking work and lets running turns finish for a grace period.
Temporal then retries the unfinished ones elsewhere, where they resume from their checkpoints.
"""

import asyncio
import logging
import os
import signal
from datetime import timedelta
from pathlib import Path

import httpx
from temporalio.worker import Worker

from .background_activities import BackgroundActivities
from .deletion import DeletionActivities
from .directory_activities import DirectoryActivities
from .environment_activities import EnvironmentActivities
from .keycloak_admin import KeycloakAdmin
from .langfuse_tracer import start_tracing
from .plugin_activities import PluginActivities
from .runs.activities import HEARTBEAT_S, RunActivities
from .runtime import open_runtime
from .schedules import (
    ensure_directory_schedule,
    ensure_plugins_schedule,
    ensure_reindex_schedule,
    ensure_sweep_schedule,
)
from .search_activities import SearchActivities
from .settings import get_settings
from .task_activities import TaskActivities
from .temporal import WORKFLOW_RUNNER, connect, keep_token_fresh
from .transient import QuietTransientFailures
from .workflows.names import AGENT_QUEUE, SYSTEM_QUEUE
from .workflows.registry import ALL_WORKFLOWS

log = logging.getLogger("gen9_agent.worker")

GRACE_S = 20
# Touched every couple of seconds while both workers run; the container healthcheck fails when it
# goes stale
ALIVE_FILE = Path(os.environ.get("WORKER_ALIVE_FILE", "/tmp/gen9-agent-worker-alive"))


async def _main() -> None:
    settings = get_settings()
    async with (
        open_runtime(settings) as runtime,
        httpx.AsyncClient(timeout=10) as http,
    ):
        keycloak = (
            KeycloakAdmin(settings, http)
            if settings.keycloak_admin_client_secret
            else None
        )
        client = await connect(settings, "gen9-agent-worker", keycloak)
        activities = RunActivities(runtime)
        deletion = DeletionActivities(runtime, client, keycloak)
        search = SearchActivities(runtime)
        directory = DirectoryActivities(runtime)
        plugin_sources = PluginActivities(runtime)
        environment = EnvironmentActivities(runtime, client)
        scheduled = TaskActivities(runtime, client)
        # A background task's notice to its chat (background_activities.py)
        tasks_told = BackgroundActivities(runtime, client)
        if runtime.environments:
            # Runs ask a chat's environment workflow for its sandbox (environments.py)
            runtime.environments.temporal = client
        if runtime.background:
            # Runs start their background tasks' runs (background.py)
            runtime.background.temporal = client
        # Runs, firings and notices ask whether their person may still have work done
        # (standing.py)
        runtime.standing.admin = keycloak
        workers = [
            Worker(
                client,
                task_queue=AGENT_QUEUE,
                activities=[activities.agent_turn],
                max_concurrent_activities=settings.worker_concurrency,
                graceful_shutdown_timeout=timedelta(seconds=GRACE_S),
                # A Stop reaches a turn only with a heartbeat's reply, and the SDK sends heartbeats
                # at most every 0.8 x the 3 s timeout (2.4 s): a fast answer finished whole after
                # Stop (manual-e2e.md, P3-C7). Every half second instead, a heartbeat call each
                max_heartbeat_throttle_interval=timedelta(seconds=HEARTBEAT_S),
                interceptors=[QuietTransientFailures()],
            ),
            Worker(
                client,
                task_queue=SYSTEM_QUEUE,
                workflows=ALL_WORKFLOWS,
                workflow_runner=WORKFLOW_RUNNER,
                activities=[
                    activities.finish_run,
                    activities.park_run,
                    activities.index_run,
                    *deletion.all(),
                    *search.all(),
                    *directory.all(),
                    *plugin_sources.all(),
                    *environment.all(),
                    *scheduled.all(),
                    *tasks_told.all(),
                ],
                graceful_shutdown_timeout=timedelta(seconds=GRACE_S),
                # A service that doesn't answer: one line an attempt, not a traceback (transient.py)
                interceptors=[QuietTransientFailures()],
            ),
        ]
        # Users deleted in Keycloak directly: the Schedule that sweeps them (needs the admin client)
        await ensure_sweep_schedule(
            client,
            settings.deleted_users_sweep_interval_s if keycloak else 0,
        )
        # Older chats made searchable by meaning, and again after `embed` changes
        await ensure_reindex_schedule(client, settings.search_reindex_interval_s)
        # The connector directory's copy of the MCP registry, synced about hourly
        await ensure_directory_schedule(client, settings.mcp_registry_sync_s)
        # The plugin sources admins added, synced daily
        await ensure_plugins_schedule(client, settings.plugin_sources_sync_s)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        running = [asyncio.create_task(w.run()) for w in workers]
        if keycloak:
            # Not in `running`: its end isn't a worker failure
            renewing = asyncio.create_task(keep_token_fresh(client, keycloak))
        log.info(
            "worker ready on %s (%d agent turns at once) and %s",
            AGENT_QUEUE,
            settings.worker_concurrency,
            SYSTEM_QUEUE,
        )
        try:
            while not stop.is_set() and not any(t.done() for t in running):
                await asyncio.to_thread(ALIVE_FILE.touch)
                try:
                    await asyncio.wait_for(stop.wait(), 2)
                except TimeoutError:
                    pass
        finally:
            log.info("worker stopping")
            if keycloak:
                renewing.cancel()
            await asyncio.gather(
                *(w.shutdown() for w in workers if w.is_running), return_exceptions=True
            )
            for task in running:
                if task.done() and not task.cancelled() and (err := task.exception()):
                    raise err  # a worker failed: exit non-zero


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Before any run traces: its media store's internal address (langfuse_tracer.py)
    start_tracing()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
