"""Probe: what happens to a Signal and to an Update sent to a workflow while no worker runs, on a
Temporal dev server (temporalio 1.33.0, `WorkflowEnvironment.start_local`, which downloads the
Temporal CLI's dev server the first time).

    uv run python explore/hitl/messages_probe.py

A person may answer a waiting run while every worker is restarting. Which message survives that?
"""

import asyncio
import time
import uuid
from datetime import timedelta

from temporalio import workflow
from temporalio.client import Client, WorkflowUpdateStage
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

QUEUE = "probe"


@workflow.defn
class Waits:
    def __init__(self) -> None:
        self.signalled: list[str] = []
        self.updated: list[str] = []

    @workflow.run
    async def run(self) -> dict:
        await workflow.wait_condition(
            lambda: len(self.signalled) + len(self.updated) >= 2
        )
        return {"signals": self.signalled, "updates": self.updated}

    @workflow.signal
    def nudge(self, what: str) -> None:
        self.signalled.append(what)

    @workflow.update
    def answer(self, what: str) -> None:
        self.updated.append(what)

    @answer.validator
    def check(self, what: str) -> None:
        if not what:
            raise ValueError("empty")


async def main() -> None:
    async with await WorkflowEnvironment.start_local(namespace="default") as env:
        client: Client = env.client
        wf_id = f"waits-{uuid.uuid4()}"
        async with Worker(
            client,
            task_queue=QUEUE,
            workflows=[Waits],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await client.start_workflow(Waits.run, id=wf_id, task_queue=QUEUE)
            await asyncio.sleep(1)
        print("worker stopped")

        t = time.monotonic()
        await handle.signal(Waits.nudge, "signal while no worker")
        print(f"signal returned in {time.monotonic() - t:.2f}s")

        t = time.monotonic()
        try:
            await handle.execute_update(
                Waits.answer,
                "update while no worker",
                id="u1",
                rpc_timeout=timedelta(seconds=5),
            )
            print("update completed?!")
        except Exception as e:  # noqa: BLE001 (the probe prints whatever happens)
            print(
                f"execute_update after {time.monotonic() - t:.2f}s: {type(e).__name__}: {e}"
            )

        t = time.monotonic()
        try:
            await handle.start_update(
                Waits.answer,
                "update (accepted stage) while no worker",
                id="u2",
                wait_for_stage=WorkflowUpdateStage.ACCEPTED,
                rpc_timeout=timedelta(seconds=5),
            )
            print("start_update accepted?!")
        except Exception as e:  # noqa: BLE001 (the probe prints whatever happens)
            print(
                f"start_update after {time.monotonic() - t:.2f}s: {type(e).__name__}: {e}"
            )

        print("worker back")
        async with Worker(
            client,
            task_queue=QUEUE,
            workflows=[Waits],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            # Is the timed-out update u1 still delivered once a worker is back?
            await asyncio.sleep(3)
            desc = await handle.describe()
            print("status after the worker came back:", desc.status.name)
            if desc.status.name == "RUNNING":
                await handle.execute_update(
                    Waits.answer, "update with a worker", id="u3"
                )
            print("result:", await handle.result())
            # A repeated Update ID returns the earlier outcome without running the handler again
            try:
                await handle.execute_update(Waits.answer, "again", id="u3")
                print("repeated update id u3 after completion: accepted")
            except Exception as e:  # noqa: BLE001 (the probe prints whatever happens)
                print(
                    f"repeated update id u3 after completion: {type(e).__name__}: {e}"
                )


asyncio.run(main())
