"""Probe: per-person MCP connectors in a Deep Agent, on the real libraries (deepagents 0.7.18,
langchain[mcp] 1.4.2, fastmcp 4.0.9), with a scripted model:

  1. tools registered per run by a middleware (LangChain's "runtime tool registration"): the
     person's connectors come in the run's context, their tools are renamed `<connector>__<tool>`
     and still reach the MCP tool by its own name
  2. annotations (`readOnlyHint`) as the adapter exposes them, from a local server
  3. approvals for tool names only known at run time: HumanInTheLoopMiddleware with a mapping
     that answers for any connector tool
  4. a real public server (DeepWiki) called through the same path

    uv run --with 'langchain[mcp]==1.4.2' python explore/connectors/probe.py
"""

import asyncio
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from fastmcp import FastMCP
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

warnings.filterwarnings("ignore")
from langchain.mcp import MCPAdapter

notes = FastMCP("Notes")
NOTES: list[str] = []


@notes.tool(annotations={"readOnlyHint": True})
def list_notes() -> str:
    """List the notes."""
    return ", ".join(NOTES) or "(none)"


@notes.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def add_note(text: str) -> str:
    """Add a note."""
    NOTES.append(text)
    return f"added: {text}"


@dataclass
class Ctx:
    connectors: dict[str, Any] = field(
        default_factory=dict
    )  # name -> MCPAdapter target


SEP = "__"


class Connectors(AgentMiddleware):
    """Adds the run's connector tools to each model call and runs them when called."""

    def __init__(self) -> None:
        super().__init__()
        self.loaded: dict[str, BaseTool] = {}

    async def tools_for(self, context: Ctx) -> list[BaseTool]:
        tools = []
        for name, target in context.connectors.items():
            for tool in await MCPAdapter(target).list_tools():
                renamed = tool.model_copy(update={"name": f"{name}{SEP}{tool.name}"})
                self.loaded[renamed.name] = renamed
                tools.append(renamed)
        return tools

    async def awrap_model_call(self, request, handler):
        extra = await self.tools_for(request.runtime.context)
        return await handler(request.override(tools=[*request.tools, *extra]))

    async def awrap_tool_call(self, request, handler):
        tool = self.loaded.get(request.tool_call["name"])
        return await handler(request.override(tool=tool) if tool else request)


def read_only(tool: BaseTool | None) -> bool:
    annotations = (
        (tool.metadata or {}).get("mcp", {}).get("tool", {}) if tool else {}
    ).get("annotations", {})
    # langchain.mcp dumps them with pydantic's field names (read_only_hint), not MCP's wire names
    return (
        annotations.get("read_only_hint") or annotations.get("readOnlyHint")
    ) is True


class ConnectorPolicy(dict):
    """An `interrupt_on` mapping that answers for any connector tool: ask unless read-only."""

    def __init__(self, connectors: Connectors) -> None:
        super().__init__()
        self.connectors = connectors
        self.config = InterruptOnConfig(
            allowed_decisions=["approve", "reject"],
            when=lambda req: (
                not read_only(self.connectors.loaded.get(req.tool_call["name"]))
            ),
        )

    def get(self, name, default=None):
        return self.config if SEP in name else super().get(name, default)

    def __getitem__(self, name):
        if SEP in name:
            return self.config
        return super().__getitem__(name)


class Scripted(BaseChatModel):
    script: list[AIMessage]
    calls: int = 0
    seen_tools: list[list[str]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self.script[self.calls % len(self.script)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append(
            [t.name if hasattr(t, "name") else t.get("name", "?") for t in tools]
        )
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def call(name, args, id):
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


async def main() -> None:
    connectors = Connectors()
    hitl = HumanInTheLoopMiddleware(interrupt_on={})
    hitl.interrupt_on = ConnectorPolicy(connectors)  # replaces the resolved mapping
    model = Scripted(
        script=[
            AIMessage("", tool_calls=[call("notes__list_notes", {}, "c1")]),
            AIMessage("", tool_calls=[call("notes__add_note", {"text": "teal"}, "c2")]),
            AIMessage(
                "",
                tool_calls=[
                    call(
                        "deepwiki__read_wiki_structure",
                        {"repoName": "langchain-ai/deepagents"},
                        "c3",
                    )
                ],
            ),
            AIMessage("Done."),
        ]
    )
    agent = create_deep_agent(
        model=model,
        checkpointer=InMemorySaver(),
        context_schema=Ctx,
        middleware=[connectors, hitl],
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    context = Ctx(
        connectors={"notes": notes, "deepwiki": "https://mcp.deepwiki.com/mcp"}
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]}, config, context=context
    )
    print("tools the model saw:", [n for n in model.seen_tools[0] if SEP in n])
    print(
        "annotations:",
        {
            n: read_only(t)
            for n, t in connectors.loaded.items()
            if n.startswith("notes")
        },
    )
    for _ in range(3):
        pending = result.get("__interrupt__")
        if not pending:
            break
        action = pending[0].value["action_requests"][0]
        print(f"approval asked for {action['name']} {action['args']}; approving")
        result = await agent.ainvoke(
            Command(resume={pending[0].id: {"decisions": [{"type": "approve"}]}}),
            config,
            context=context,
        )
    for m in result["messages"]:
        if isinstance(m, ToolMessage):
            print(f"  {m.name}: {str(m.content)[:100]!r}")
    print("notes now:", NOTES, "| final:", result["messages"][-1].text)


asyncio.run(main())
