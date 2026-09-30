"""The Temporal Activities of a run (`workflows/runs.py`), bound to the worker's Runtime.

`agent_turn` is the whole agent turn: a long Activity that heartbeats every half second from a side
task, so Temporal notices a dead worker within the 3 s heartbeat timeout and can deliver a Stop.
It returns `{"status", "pending"}`: a turn that paused for the person names what it waits for.
`finish_run` records a cancellation or a final failure, and `index_run` makes a finished run
searchable (runs/indexing.py). All are safe to repeat.
"""

import asyncio
import logging
import uuid
from typing import Any

from temporalio import activity

from .. import notices
from ..environments import RUNNING, STOPPED, stop_left
from ..runtime import Runtime
from ..workflows.names import AGENT_TURN, FINISH_RUN, INDEX_RUN, PARK_RUN
from ..workflows.runs import FinishInput, ParkInput, RunInput
from . import store
from .executor import execute
from .indexing import index_run

log = logging.getLogger(__name__)

# How often the turn heartbeats, and the most its worker holds one back (worker.py): a Stop
# arrives within about this
HEARTBEAT_S = 0.5


class RunActivities:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    @activity.defn(name=AGENT_TURN)
    async def agent_turn(self, run: RunInput) -> dict[str, Any]:
        info = activity.info()
        log.info("run %s: attempt %d", run.run_id, info.attempt)
        # The commands this attempt runs in the chat's environment go with each heartbeat; one
        # retried after a crashed worker stops those the last attempt left running, before the
        # turn resumes and runs them again (environments.py, RUNNING)
        running: dict[str, dict[str, str]] = {}
        RUNNING.set(running)
        left = info.heartbeat_details[0] if info.heartbeat_details else None
        if left and self.runtime.environments is not None:
            STOPPED.set(set(await stop_left(self.runtime.settings, left)))
        beating = asyncio.create_task(_heartbeat(running))
        try:
            return await execute(self.runtime, uuid.UUID(run.run_id), info.attempt)
        except asyncio.CancelledError:
            details = activity.cancellation_details()
            why = (
                "stopped"
                if details and details.cancel_requested
                else "worker shutting down, to be retried"
                if details and details.worker_shutdown
                else f"cancelled ({details})"
            )
            log.info("run %s: attempt %d %s", run.run_id, info.attempt, why)
            raise
        finally:
            beating.cancel()

    @activity.defn(name=INDEX_RUN)
    async def index_run(self, run: RunInput) -> bool:
        indexed = await index_run(self.runtime, uuid.UUID(run.run_id), run.user_sub)
        log.info(
            "run %s: %s",
            run.run_id,
            "indexed for search" if indexed else "nothing to index",
        )
        return indexed

    @activity.defn(name=PARK_RUN)
    async def park_run(self, park: ParkInput) -> str:
        input_id = await store.park(
            self.runtime.engine, uuid.UUID(park.run_id), park.error
        )
        log.info("run %s: waiting for Retry after: %s", park.run_id, park.error[:200])
        return input_id

    @activity.defn(name=FINISH_RUN)
    async def finish_run(self, finish: FinishInput) -> None:
        if await store.end(
            self.runtime.engine, uuid.UUID(finish.run_id), finish.status, finish.error
        ):
            log.info("run %s: %s", finish.run_id, finish.status)
        # A background run that didn't finish tells its person (stopped: they stopped it)
        if finish.status in ("error", "expired"):
            await notices.notify_safely(
                self.runtime, uuid.UUID(finish.run_id), "failed"
            )


async def _heartbeat(running: dict[str, dict[str, str]]) -> None:
    while True:
        activity.heartbeat(dict(running))
        await asyncio.sleep(HEARTBEAT_S)
