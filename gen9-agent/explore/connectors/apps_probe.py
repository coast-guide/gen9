"""MCP Apps on the installed libraries (langchain 1.4.2, mcp 2.2, fastmcp 4.0.9): what a host that
reaches servers through `MCPAdapter` gets.

A FastMCP server with a UI tool (`show_board`, visible to the model and the app), an app-only
tool (`move`), and its `ui://` resource, served over streamable HTTP. Asked with and without the
client advertising `io.modelcontextprotocol/ui`:
- does the server see the client's support, and list the tools differently?
- does the LangChain tool keep `_meta.ui` (resourceUri, visibility)?
- does a call keep `structuredContent` (the artifact) and the result's `_meta`?
- can the client read the `ui://` resource, with its `_meta.ui` (csp)?

    uv run python explore/connectors/apps_probe.py
"""

import asyncio
import json
import socket

import uvicorn
from fastmcp import Client, Context, FastMCP
from fastmcp.apps.config import UI_EXTENSION_ID, UI_MIME_TYPE, AppConfig, ResourceCSP
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.extension import advertise

HTML = "<!doctype html><html><body><p id=board>…</p></body></html>"

server = FastMCP("board")


@server.tool(app=AppConfig(resource_uri="ui://board/app.html"))
async def show_board(size: int, ctx: Context) -> dict:
    """Show a board of the given size."""
    extensions = (
        (ctx.session.client_params.capabilities.extensions or {})
        if ctx.session.client_params
        else {}
    )
    return {
        "size": size,
        "cells": [0] * size,
        "client_says_ui": UI_EXTENSION_ID in extensions,
    }


@server.tool(app=AppConfig(resource_uri="ui://board/app.html", visibility=["app"]))
async def move(cell: int) -> str:
    """Play a move (the app only)."""
    return f"played {cell}"


@server.resource(
    "ui://board/app.html",
    mime_type=UI_MIME_TYPE,
    app=AppConfig(
        csp=ResourceCSP(connect_domains=["https://api.example.com"]),
        prefers_border=True,
    ),
)
def board_html() -> str:
    return HTML


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def ask(url: str, advertised: bool) -> None:
    from langchain.mcp import MCPAdapter

    extensions = (
        [advertise(UI_EXTENSION_ID, {"mimeTypes": [UI_MIME_TYPE]})]
        if advertised
        else None
    )
    client = Client(StreamableHttpTransport(url), extensions=extensions)
    print(f"\n== client advertises the UI extension: {advertised}")
    tools = await MCPAdapter(client).list_tools()
    for tool in tools:
        meta = (tool.metadata or {}).get("mcp", {}).get("tool", {}).get("_meta")
        print(f"tool {tool.name}: _meta {json.dumps(meta)}")
    board = next(t for t in tools if t.name == "show_board")
    message = await board.ainvoke(
        {"type": "tool_call", "id": "1", "name": "show_board", "args": {"size": 3}}
    )
    print(f"call: content {message.content!r}")
    print(f"call: artifact {message.artifact!r}")
    async with client:
        raw = await client.call_tool_mcp("show_board", {"size": 2})
        print(
            f"raw result: structuredContent {raw.structured_content!r}, _meta {raw.meta!r}"
        )
        listed = await client.list_resources()
        print(f"resources/list: {[(str(r.uri), r.mime_type, r.meta) for r in listed]}")
        read = await client.read_resource_mcp("ui://board/app.html")
        for c in read.contents:
            print(
                f"resources/read: {c.mime_type} meta {c.meta} text {getattr(c, 'text', '')[:40]!r}"
            )


async def main() -> None:
    port = free_port()
    config = uvicorn.Config(
        server.http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    running = uvicorn.Server(config)
    task = asyncio.create_task(running.serve())
    # uvicorn says it started only through this flag
    while not running.started:  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    try:
        for advertised in (False, True):
            await ask(f"http://127.0.0.1:{port}/mcp", advertised)
    finally:
        running.should_exit = True
        await task


asyncio.run(main())
