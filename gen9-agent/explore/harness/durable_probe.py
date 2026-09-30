"""Probe: a run killed mid-tool resumes in another process from the Postgres checkpoint.

    uv run --env-file .env --env-file postgres.local.env python explore/harness/durable_probe.py start <thread>
    kill -9 <that pid>   # while step 2 runs
    uv run --env-file .env --env-file postgres.local.env python explore/harness/durable_probe.py resume <thread>
    uv run --env-file .env --env-file postgres.local.env python explore/harness/durable_probe.py report <thread>

Each tool call appends "<pid> <step> start|finish" to explore/out/durable-<thread>.log, so the log
shows which work ran where. `report` prints the thread's tool results and deletes the thread.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from gen9_agent.agent import checkpoint_pool
from gen9_agent.settings import get_settings

MODE, THREAD = sys.argv[1], sys.argv[2]
LOG = Path(__file__).parent.parent / "out" / f"durable-{THREAD}.log"


def note(line: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} {line}\n")


@tool
async def slow_step(n: int) -> str:
    """Do step n of the job. Takes a few seconds."""
    note(f"step {n} start")
    await asyncio.sleep(8)
    note(f"step {n} finish")
    return f"step {n} ok"


async def main() -> None:
    pool = checkpoint_pool(get_settings())
    await pool.open(wait=True)
    try:
        checkpointer = AsyncPostgresSaver(pool)
        agent = create_deep_agent(
            model="openai:gpt-5.5",
            tools=[slow_step],
            system_prompt="Call slow_step with n=1, then n=2, then n=3: one call per turn, in order, "
            "never in parallel. Then reply with the word done.",
            checkpointer=checkpointer,
        )
        config = {"configurable": {"thread_id": THREAD}}
        if MODE == "start":
            note("start")
            await agent.ainvoke(
                {"messages": [{"role": "user", "content": "Run the job."}]}, config
            )
            note("start finished (not killed?)")
        elif MODE == "resume":
            state = await agent.aget_state(config)
            note(
                f"resume: next={list(state.next)} tasks={[t.name for t in state.tasks]}"
            )
            result = await agent.ainvoke(None, config)
            note(f"resume finished: {result['messages'][-1].text!r}")
        elif MODE == "report":
            state = await agent.aget_state(config)
            tools = [m.content for m in state.values["messages"] if m.type == "tool"]
            ais = sum(1 for m in state.values["messages"] if m.type == "ai")
            print("tool results in history:", tools)
            print("ai messages in history:", ais)
            history = [s async for s in agent.aget_state_history(config)]
            print("checkpoints:", len(history))
            await checkpointer.adelete_thread(THREAD)
            print("thread deleted")
    finally:
        await pool.close()


asyncio.run(main())
