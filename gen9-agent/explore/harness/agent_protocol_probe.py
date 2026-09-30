"""Probe: Deep Agents' async subagents against a minimal Agent Protocol server of our own.

AsyncSubAgentMiddleware (deepagents 0.7.18) calls only five endpoints through langgraph-sdk:
POST /threads, GET /threads/{id}, POST /threads/{id}/runs, GET /threads/{id}/runs/{run},
POST /threads/{id}/runs/{run}/cancel. This serves exactly those for one in-process Deep Agent, then
has a supervisor launch it in the background, keep talking, and collect the result.

    uv run --env-file .env python explore/harness/agent_protocol_probe.py
"""

import asyncio
import uuid

import uvicorn
from deepagents import AsyncSubAgent, create_deep_agent
from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import InMemorySaver

PORT = 18998
researcher = create_deep_agent(
    model="openai:gpt-5.5",
    system_prompt="Answer in one short line.",
    checkpointer=InMemorySaver(),
)
threads: dict[
    str, dict
] = {}  # thread_id -> {"runs": {run_id: run}, "task": asyncio.Task | None}
calls: list[str] = []
app = FastAPI()


@app.middleware("http")
async def record(request, call_next):
    calls.append(f"{request.method} {request.url.path}")
    return await call_next(request)


@app.post("/threads")
async def create_thread(body: dict | None = None):
    thread_id = str(uuid.uuid4())
    threads[thread_id] = {"runs": {}, "task": None}
    return {"thread_id": thread_id, "status": "idle", "metadata": {}, "values": {}}


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    if thread_id not in threads:
        raise HTTPException(404)
    state = await researcher.aget_state({"configurable": {"thread_id": thread_id}})
    messages = [
        {"type": m.type, "content": m.text} for m in state.values.get("messages", [])
    ]
    return {"thread_id": thread_id, "values": {"messages": messages}}


@app.post("/threads/{thread_id}/runs")
async def create_run(thread_id: str, body: dict):
    thread = threads[thread_id]
    if (
        thread["task"]
        and not thread["task"].done()
        and body.get("multitask_strategy") == "interrupt"
    ):
        thread["task"].cancel()
    run_id = str(uuid.uuid4())
    run = {"run_id": run_id, "thread_id": thread_id, "status": "pending"}
    thread["runs"][run_id] = run

    async def execute():
        run["status"] = "running"
        try:
            await researcher.ainvoke(
                body["input"], {"configurable": {"thread_id": thread_id}}
            )
            run["status"] = "success"
        except asyncio.CancelledError:
            run["status"] = "interrupted"
        except Exception as e:  # noqa: BLE001
            run["status"], run["error"] = "error", str(e)

    thread["task"] = asyncio.create_task(execute())
    return run


@app.get("/threads/{thread_id}/runs/{run_id}")
async def get_run(thread_id: str, run_id: str):
    return threads[thread_id]["runs"][run_id]


@app.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel_run(thread_id: str, run_id: str):
    task = threads[thread_id]["task"]
    if task and not task.done():
        task.cancel()
    return {}


async def main() -> None:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 (uvicorn exposes a flag, no event to await)
        await asyncio.sleep(0.1)
    supervisor = create_deep_agent(
        model="openai:gpt-5.5",
        system_prompt="Delegate research questions to the researcher as a background task. "
        "When asked for results, check the task. Keep replies to one line.",
        subagents=[
            AsyncSubAgent(
                name="researcher",
                description="Answers a factual question in one line.",
                graph_id="researcher",
                url=f"http://127.0.0.1:{PORT}",
            )
        ],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "supervisor"}}
    turns = [
        "In the background, ask the researcher for the three smallest primes above 100.",
        "While that runs: what is 12 * 12?",
        "Now check the background task and give me its result.",
    ]
    for turn in turns:
        if turn.startswith("Now"):
            await asyncio.sleep(15)
        result = await supervisor.ainvoke(
            {"messages": [{"role": "user", "content": turn}]}, config
        )
        print(f"> {turn}\n  {result['messages'][-1].text}")
    print("tasks:", {k: v["status"] for k, v in result.get("async_tasks", {}).items()})
    shapes = {
        " ".join(
            c.split(" ")[:1]
            + [
                "/".join(
                    "{id}" if len(p) == 36 else p for p in c.split(" ")[1].split("/")
                )
            ]
        )
        for c in calls
    }
    print("server saw:", sorted(shapes))
    server.should_exit = True
    await serving


asyncio.run(main())
