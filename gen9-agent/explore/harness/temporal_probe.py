"""Probe: Temporal as Gen9's durable backbone, with a Deep Agent run as one heartbeating Activity.

Settles three questions (NOTES.md, "Temporal"):
  1. A worker killed mid-run: Temporal retries the Activity on another worker once its heartbeat
     times out, and the run resumes from the LangGraph Postgres checkpoint (same run_id).
  2. Cancelling the Workflow reaches the running Activity (it must heartbeat), which can tell a
     cancel request from a worker shutdown.
  3. A Workflow waits on an Update (an approval) durably, across a worker restart.

Needs a Temporal server (TEMPORAL_ADDRESS, default 127.0.0.1:17233) and gen9-postgres:
    uv run --with temporalio==1.33.0 --env-file .env --env-file postgres.local.env \\
      --env-file keycloak.local.env python explore/harness/temporal_probe.py worker
    ... start <id> | cancel <id> | approval <id> | approve <id> | describe <id>
Each tool step appends "<pid> step n start|finish" to explore/out/temporal-<id>.log.
"""

import asyncio
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from deepagents import create_deep_agent
    from langchain_core.tools import tool
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from gen9_agent.agent import checkpoint_pool
    from gen9_agent.settings import get_settings

ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:17233")
QUEUE = "gen9-probe"
OUT = Path(__file__).parent.parent / "out"


def note(run: str, line: str) -> None:
    OUT.mkdir(exist_ok=True)
    with (OUT / f"temporal-{run}.log").open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} {line}\n")


@activity.defn
async def agent_turn(run: str) -> str:
    """One agent turn. Heartbeats every 2 s so Temporal notices a dead worker and can deliver a
    cancel; the same run id on every attempt lets LangGraph resume from the checkpoint."""
    info = activity.info()
    note(run, f"attempt {info.attempt} started")

    @tool
    async def slow_step(n: int) -> str:
        """Do step n of the job. Takes a few seconds."""
        note(run, f"step {n} start")
        await asyncio.sleep(6)
        note(run, f"step {n} finish")
        return f"step {n} ok"

    async def heartbeat_forever() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(2)

    pool = checkpoint_pool(get_settings())
    await pool.open(wait=True)
    beating = asyncio.create_task(heartbeat_forever())
    try:
        agent = create_deep_agent(
            model="openai:gpt-5.5",
            tools=[slow_step],
            system_prompt="Call slow_step with n=1, then n=2, then n=3: one call per turn, in order, "
            "never in parallel. Then reply with the word done.",
            checkpointer=AsyncPostgresSaver(pool),
        )
        config = {"configurable": {"thread_id": run}, "metadata": {"run_id": run}}
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "Run the job."}]}, config
        )
        note(run, f"attempt {info.attempt} finished: {result['messages'][-1].text!r}")
        return result["messages"][-1].text
    except asyncio.CancelledError:
        details = activity.cancellation_details()
        why = (
            "cancel requested"
            if details and details.cancel_requested
            else "worker shutdown"
            if details and details.worker_shutdown
            else f"other ({details})"
        )
        note(run, f"attempt {info.attempt} cancelled: {why}")
        raise
    finally:
        beating.cancel()
        await pool.close()


@workflow.defn
class RunWorkflow:
    @workflow.run
    async def run(self, run: str) -> str:
        return await workflow.execute_activity(
            agent_turn,
            run,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


@workflow.defn
class ApprovalWorkflow:
    """Waits (durably) for a person's decision, the way a run waits for an approval."""

    def __init__(self) -> None:
        self.decision: str | None = None

    @workflow.run
    async def run(self, request: str) -> str:
        await workflow.wait_condition(lambda: self.decision is not None)
        return f"{request}: {self.decision}"

    @workflow.query
    def pending(self) -> bool:
        return self.decision is None

    @workflow.update
    async def decide(self, decision: str) -> str:
        self.decision = decision
        return "recorded"


async def main() -> None:
    mode = sys.argv[1]
    client = await Client.connect(ADDRESS)
    if mode == "worker":
        print(f"worker pid {os.getpid()}", flush=True)
        worker = Worker(
            client,
            task_queue=QUEUE,
            workflows=[RunWorkflow, ApprovalWorkflow],
            activities=[agent_turn],
            graceful_shutdown_timeout=timedelta(seconds=5),
        )
        await worker.run()
    elif mode == "start":
        await client.start_workflow(
            RunWorkflow.run, sys.argv[2], id=sys.argv[2], task_queue=QUEUE
        )
        print("started", sys.argv[2])
    elif mode == "cancel":
        await client.get_workflow_handle(sys.argv[2]).cancel()
        print("cancel requested")
    elif mode == "approval":
        await client.start_workflow(
            ApprovalWorkflow.run, "delete old files", id=sys.argv[2], task_queue=QUEUE
        )
        print(
            "waiting for a decision:",
            await client.get_workflow_handle(sys.argv[2]).query(
                ApprovalWorkflow.pending
            ),
        )
    elif mode == "approve":
        handle = client.get_workflow_handle(sys.argv[2])
        print(
            "update:", await handle.execute_update(ApprovalWorkflow.decide, "approved")
        )
        print("result:", await handle.result())
    elif mode == "describe":
        handle = client.get_workflow_handle(sys.argv[2])
        desc = await handle.describe()
        print("status:", desc.status.name if desc.status else None)
        attempts = [
            (
                e.event_type,
                getattr(e.activity_task_started_event_attributes, "attempt", None),
            )
            async for e in handle.fetch_history_events()
            if "ACTIVITY_TASK" in str(e.event_type) or e.event_type in (26,)
        ]
        print("activity events:", [(str(t).split(".")[-1], a) for t, a in attempts])


# Temporal's workflow sandbox re-imports this module to validate the workflows: no side effects
# at import time
if __name__ == "__main__":
    asyncio.run(main())
