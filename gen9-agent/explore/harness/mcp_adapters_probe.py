"""Probe: the standalone langchain-mcp-adapters (pinned to mcp<2) against the probe server.

uv run --no-project --with langchain-mcp-adapters==0.3.2 python explore/harness/mcp_adapters_probe.py
"""

import asyncio
import importlib.metadata as md

from langchain_mcp_adapters.client import MultiServerMCPClient

URL = "http://127.0.0.1:18999/mcp"


async def main() -> None:
    print(
        "langchain-mcp-adapters",
        md.version("langchain-mcp-adapters"),
        "mcp",
        md.version("mcp"),
    )
    client = MultiServerMCPClient(
        {"probe": {"transport": "streamable_http", "url": URL}}
    )
    async with client.session("probe") as session:
        init = getattr(session, "_server_capabilities", None)
        print(
            "session type:",
            type(session).__name__,
            "| capabilities known:",
            init is not None,
        )
    tools = await client.get_tools()
    print("tools:", [t.name for t in tools])
    add = next(t for t in tools if t.name == "add")
    print("add(2,3) ->", await add.ainvoke({"a": 2, "b": 3}))
    book = next(t for t in tools if t.name == "book_table")
    try:
        print("book_table(4) ->", await book.ainvoke({"people": 4}))
    except Exception as e:  # noqa: BLE001  # no elicitation handler configured
        print("book_table(4) raised:", type(e).__name__, str(e)[:160])


asyncio.run(main())
