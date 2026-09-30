"""Probe: Stop pressed just as a turn pauses for the person, so the cancel races the paused turn's
completion. It runs 40 times on a Temporal dev server (`start_local`), with the real RunWorkflow
and the unit tests' fake Activities, and dumps the history if a run hangs.

    uv run python -u explore/hitl/stop_race_probe.py
"""

import asyncio
import sys

from temporalio.common import SearchAttributeKey
from temporalio.testing import WorkflowEnvironment

sys.path.insert(0, "tests")
from test_run_workflow import FakeActivities, a_run, running

TRIES = 40


async def main() -> None:
    async with await WorkflowEnvironment.start_local(
        search_attributes=[SearchAttributeKey.for_keyword("Gen9RunState")]
    ) as env:
        for n in range(TRIES):
            fake = FakeActivities("waiting:a")
            async with running(env, fake, a_run()) as handle:
                await fake.paused.wait()  # the turn is returning `waiting` right now
                await handle.cancel()
                try:
                    async with asyncio.timeout(8):
                        await handle.result()
                    print(n, "completed instead of cancelled")
                except TimeoutError:
                    print(n, "HUNG; finished:", fake.finished)
                    for event in (await handle.fetch_history()).events:
                        print("  ", event.event_id, event.WhichOneof("attributes"))
                    return
                except Exception as e:  # noqa: BLE001 (the probe prints whatever happens)
                    print(n, type(e).__name__, [f.status for f in fake.finished])
        print(f"no hang in {TRIES}")


asyncio.run(main())
