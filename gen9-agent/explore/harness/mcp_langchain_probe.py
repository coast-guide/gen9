"""Probe: langchain.mcp (FastMCP-based, in langchain[mcp]) with a Deep Agent.

Checks which protocol era it negotiates with the probe server, and whether an elicitation during a
tool call pauses the run as a LangGraph interrupt that can be answered and resumed.

    uv run --no-project --env-file .env --with 'langchain[mcp]==1.4.2' --with deepagents==0.7.18 \
      --with langchain-openai python explore/harness/mcp_langchain_probe.py
"""

import asyncio
import importlib.metadata as md
import warnings

from deepagents import create_deep_agent
from fastmcp.client import Client
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

warnings.filterwarnings("ignore")
from langchain.mcp import MCPAdapter

URL = "http://127.0.0.1:18999/mcp"


async def main() -> None:
    print(
        "langchain",
        md.version("langchain"),
        "fastmcp",
        md.version("fastmcp"),
        "mcp",
        md.version("mcp"),
    )
    for mode in ("auto", "legacy"):
        async with Client(URL, mode=mode) as client:
            print(
                f"mode={mode}: negotiated",
                client.protocol_version if hasattr(client, "protocol_version") else "?",
            )

    async with MCPAdapter(URL) as adapter:
        tools = await adapter.list_tools()
        print("tools:", [t.name for t in tools])
        agent = create_deep_agent(
            model="openai:gpt-5.5",
            tools=tools,
            system_prompt="When asked to book, call book_table right away with only the party size: the tool itself asks the user for the date. Reply in one sentence.",
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "probe-1"}}
        paused = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "Book a table for 4."}]}, config
        )
        interrupts = paused.get("__interrupt__") or []
        print("interrupted:", bool(interrupts))
        if not interrupts:
            print("final:", paused["messages"][-1].text)
            return
        value = interrupts[0].value
        print(
            "interrupt value keys:",
            list(value) if isinstance(value, dict) else type(value).__name__,
        )
        requests = value.get("requests", []) if isinstance(value, dict) else []
        print(
            "requests:",
            [{k: r.get(k) for k in ("key", "message", "type")} for r in requests],
        )
        answer = {"action": "accept", "content": {"date": "2026-10-02"}}
        done = await agent.ainvoke(
            Command(resume={"responses": {requests[0]["key"]: answer}}), config
        )
        print("final:", done["messages"][-1].text)


asyncio.run(main())
