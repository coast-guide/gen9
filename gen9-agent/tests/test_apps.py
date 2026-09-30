"""MCP Apps (apps.py): which connector tools the model sees, what a UI tool's step carries, and
what a View may read and call through Gen9. A real FastMCP server with a View, in memory."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from fastmcp import Client, FastMCP
from fastmcp.apps.config import UI_MIME_TYPE, AppConfig, ResourceCSP
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from test_connectors import Scripted, call

from gen9_agent import apps
from gen9_agent.api.threads import conversation
from gen9_agent.connector_net import ConnectorError
from gen9_agent.connectors import (
    SEP,
    AskFirst,
    ConnectorApprovals,
    ConnectorTools,
    _Loaded,
    describe,
)
from gen9_agent.memory import Gen9Context
from gen9_agent.runs.events import EventMapper

board = FastMCP("Board")
MOVES: list[int] = []


@board.tool(app=AppConfig(resource_uri="ui://board/app.html"))
def show_board(size: int) -> dict:
    """Show a board."""
    return {"size": size, "cells": [0] * size}


@board.tool(app=AppConfig(resource_uri="ui://board/app.html", visibility=["app"]))
def move(cell: int) -> str:
    """Play a move (the View only)."""
    MOVES.append(cell)
    return f"played {cell}"


@board.tool(app=AppConfig(visibility=["model"]), annotations={"readOnlyHint": True})
def score() -> str:
    """The score (the model only)."""
    return "0-0"


@board.resource(
    "ui://board/app.html",
    mime_type=UI_MIME_TYPE,
    app=AppConfig(csp=ResourceCSP(connect_domains=["https://api.example.com"])),
)
def board_view() -> str:
    return "<!doctype html><p>board</p>"


@board.resource("ui://board/not-an-app.html", mime_type="text/html")
def not_an_app() -> str:
    return "<p>plain</p>"


ROW = SimpleNamespace(
    id=uuid.uuid4(),
    name="board",
    url="https://board.example.com/mcp",
    status="ready",
    updated_at=datetime.now(UTC),
    policy="never",
    tools=[],  # what the person kept: set from the board's listing (kept())
    changed=None,
)


async def kept() -> list[dict]:
    """The board's tools as the person kept them when they connected it (connectors.pin)."""
    from langchain.mcp import MCPAdapter

    return [describe(t) for t in await MCPAdapter(board).list_tools()]


class InMemoryBoard(ConnectorTools):
    """The person `sub-a` has the Board server as connector `board`, policy "never". The
    database and the network are replaced; the rest is the real middleware."""

    async def tools_of(self, sub: str) -> dict[str, _Loaded]:
        from langchain.mcp import MCPAdapter

        loaded = {}
        for t in await MCPAdapter(board).list_tools():
            name = f"board{SEP}{t.name}"
            loaded[name] = _Loaded(
                t.model_copy(update={"name": name}), "never", ROW.id, "board"
            )
        self._people[sub] = loaded
        return loaded

    async def _client(self, row, sub) -> Client:
        return Client(board)


def tool(meta: dict | None) -> StructuredTool:
    t = StructuredTool.from_function(lambda: "", name="t", description="t")
    t.metadata = {"mcp": {"tool": {"_meta": meta}}} if meta is not None else None
    return t


@pytest.mark.parametrize(
    ("meta", "model", "app"),
    [
        (None, True, True),  # a plain tool: no View, visible to both
        ({"ui": {"resourceUri": "ui://a/b"}}, True, True),
        ({"ui": {"resourceUri": "ui://a/b", "visibility": ["app"]}}, False, True),
        ({"ui": {"visibility": ["model"]}}, True, False),
        ({"ui/resourceUri": "ui://a/b"}, True, True),  # the deprecated flat key
    ],
)
def test_who_may_call_a_tool(meta, model, app) -> None:
    assert apps.for_model(tool(meta)) is model
    assert apps.for_app(tool(meta)) is app


def test_a_view_address_must_be_ui() -> None:
    found = apps.app_of(tool({"ui": {"resourceUri": "https://evil.example/app"}}))
    assert found is not None and found["resource_uri"] is None


def test_a_step_carries_its_view_and_a_bounded_result() -> None:
    message = ToolMessage(
        content=[{"type": "text", "text": "ok"}],
        tool_call_id="c1",
        artifact={"structured_content": {"cells": [0, 0]}},
    )
    tagged = apps.tagged(message, "id-1", "board", "ui://board/app.html", {"size": 2})
    assert apps.step_app(tagged) == {
        "connector_id": "id-1",
        "connector": "board",
        "resource_uri": "ui://board/app.html",
        "input": {"size": 2},
        "result": {
            "content": [{"type": "text", "text": "ok"}],
            "isError": False,
            "structuredContent": {"cells": [0, 0]},
        },
    }
    huge = tagged.model_copy(
        update={"artifact": {**tagged.artifact, "structured_content": "x" * 300_000}}
    )
    assert "structuredContent" not in apps.step_app(huge)["result"]
    assert apps.step_app(message) is None  # not tagged: no View


@pytest.mark.asyncio
async def test_the_model_never_sees_a_views_own_tools_and_a_ui_step_carries_its_view() -> (
    None
):
    connectors = InMemoryBoard(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    model = Scripted(
        script=[
            call("board__show_board", {"size": 3}, "c1"),
            call("board__move", {"cell": 1}, "c2"),
            AIMessage("Done."),
        ]
    )
    agent = create_deep_agent(
        model=model,
        checkpointer=InMemorySaver(),
        context_schema=Gen9Context,
        middleware=[connectors, ConnectorApprovals(connectors)],
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    done = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config,
        context=Gen9Context("sub-a"),
    )
    offered = model.offered[0]
    assert "board__show_board" in offered and "board__score" in offered
    assert "board__move" not in offered
    shown, refused = [m for m in done["messages"] if isinstance(m, ToolMessage)]
    # The model can't call a View's own tool even by naming it
    assert refused.status == "error" and "isn't available" in refused.text
    assert MOVES == []
    # The UI tool's result names its View, on the event and on the saved chat's step
    [completed] = [
        e
        for e in EventMapper().map(
            {"type": "updates", "ns": (), "data": {"tools": {"messages": [shown]}}}
        )
        if e.type == "tool.completed"
    ]
    app = completed.data["app"]
    assert app["connector"] == "board" and app["resource_uri"] == "ui://board/app.html"
    assert app["input"] == {"size": 3}
    assert app["result"]["structuredContent"] == {"size": 3, "cells": [0, 0, 0]}
    [answer] = [m for m in conversation(done) if m.role == "assistant" and m.steps]
    assert answer.steps[0].app == app
    assert answer.steps[1].app is None


@pytest.mark.asyncio
async def test_a_view_reads_its_resource_with_its_csp() -> None:
    connectors = InMemoryBoard(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    read = await connectors.app_resource(ROW, "sub-a", "ui://board/app.html")  # ty: ignore[invalid-argument-type]
    [content] = read["contents"]
    assert content["mimeType"] == UI_MIME_TYPE
    assert content["_meta"]["ui"]["csp"] == {
        "connectDomains": ["https://api.example.com"]
    }
    with pytest.raises(ConnectorError, match="isn't an MCP App"):
        await connectors.app_resource(ROW, "sub-a", "ui://board/not-an-app.html")  # ty: ignore[invalid-argument-type]


class Unreachable(InMemoryBoard):
    """The board's server down: nothing listens where its connector points."""

    async def _client(self, row, sub) -> Client:
        return Client("http://127.0.0.1:9/mcp")


@pytest.mark.asyncio
async def test_a_server_that_refuses_or_is_down_is_said_plainly_not_a_500() -> None:
    """Schemathesis (P6-B1): a server answering an app's read with an MCP error ("Method not found"
    there, "Unknown resource" here) was a 500, as a server down would have been."""
    connectors = InMemoryBoard(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ConnectorError, match="^The server refused: "):
        await connectors.app_resource(ROW, "sub-a", "ui://board/missing.html")  # ty: ignore[invalid-argument-type]
    down = Unreachable(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ConnectorError, match="couldn't reach"):
        await down.app_resource(ROW, "sub-a", "ui://board/app.html")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ConnectorError, match="couldn't reach"):
        await down.keep(ROW, "sub-a", set())  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_a_view_calls_only_its_tools_and_under_the_policy() -> None:
    connectors = InMemoryBoard(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    ROW.tools = await kept()
    result = await connectors.app_call(ROW, "sub-a", "move", {"cell": 2}, allowed=False)  # ty: ignore[invalid-argument-type]
    assert result["content"][0]["text"] == "played 2" and MOVES == [2]
    # A model-only tool: refused, whatever the person allowed
    with pytest.raises(ConnectorError, match="doesn't let its app use score"):
        await connectors.app_call(ROW, "sub-a", "score", {}, allowed=True)  # ty: ignore[invalid-argument-type]
    # The policy asks: not until the person allows it
    asking = SimpleNamespace(**{**vars(ROW), "policy": "ask"})
    with pytest.raises(AskFirst, match="Allow board's app to use move"):
        await connectors.app_call(asking, "sub-a", "move", {"cell": 3}, allowed=False)  # ty: ignore[invalid-argument-type]
    assert MOVES == [2]
    await connectors.app_call(asking, "sub-a", "move", {"cell": 3}, allowed=True)  # ty: ignore[invalid-argument-type]
    assert MOVES == [2, 3]


@pytest.mark.asyncio
async def test_a_view_cant_call_a_tool_that_changed_until_the_person_looks() -> None:
    connectors = InMemoryBoard(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    # `move` as the person kept it said something else: it changed since (P5-C4)
    tools = [{**t, "pin": "0" * 64} if t["name"] == "move" else t for t in await kept()]
    row = SimpleNamespace(
        **{**vars(ROW), "tools": tools, "changed": [{"name": "move"}]}
    )
    with pytest.raises(ConnectorError, match="move changed since you connected board"):
        await connectors.app_call(row, "sub-a", "move", {"cell": 5}, allowed=True)  # ty: ignore[invalid-argument-type]
    assert 5 not in MOVES


@pytest.mark.asyncio
async def test_a_views_call_answers_no_more_than_a_resource_may_hold(
    monkeypatch,
) -> None:
    # Bounded as a resource is: the browser gets it all (gen9-learn.md, M9, F16)
    import gen9_agent.connectors as module

    connectors = InMemoryBoard(engine=None, vault=None, allow_private=False)  # ty: ignore[invalid-argument-type]
    ROW.tools = await kept()
    monkeypatch.setattr(module, "MAX_APP_RESOURCE", 20)
    with pytest.raises(ConnectorError, match="move's answer is too large to show"):
        await connectors.app_call(ROW, "sub-a", "move", {"cell": 4}, allowed=True)  # ty: ignore[invalid-argument-type]
