"""What the worker's stream (`stream_mode=["messages", "updates"]`, `subgraphs=True`, v2 parts)
shows of a subagent's web search: the namespaces, where the search's ToolMessage (with its
artifact, the pages) arrives, and in which order against the `task` tool's own ToolMessage.
Real deepagents, a scripted model, no network. Run: uv run python explore/subagent_sources/probe.py
"""

import asyncio

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field


@tool(response_format="content_and_artifact")
async def web_search(query: str) -> tuple[str, list[dict]]:
    """Search the web."""
    return f"results for {query}", [
        {"url": f"https://example.org/{query}", "title": query}
    ]


class Scripted(BaseChatModel):
    """Answers each call with the next scripted message: the parent and its subagents share it."""

    script: list[AIMessage] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def call(name: str, args: dict, id: str) -> dict:
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


async def main(parallel: bool) -> None:
    tasks = [
        call("task", {"description": "check A", "subagent_type": "fact-checker"}, "t1")
    ]
    if parallel:
        tasks.append(
            call(
                "task",
                {"description": "check B", "subagent_type": "fact-checker"},
                "t2",
            )
        )
    script = [AIMessage("", tool_calls=tasks)]
    for n, _ in enumerate(tasks):
        script += [
            AIMessage("", tool_calls=[call("web_search", {"query": f"q{n}"}, f"s{n}")]),
            AIMessage(f"verdict {n}"),
        ]
    script.append(AIMessage("final"))
    agent = create_deep_agent(
        model=Scripted(script=script),
        subagents=[
            {
                "name": "fact-checker",
                "description": "checks claims",
                "system_prompt": "check",
                "tools": [web_search],
            }
        ],
    )
    print(f"--- parallel={parallel}")
    async for part in agent.astream(
        {"messages": [{"role": "user", "content": "go"}]},
        stream_mode=["messages", "updates"],
        subgraphs=True,
        version="v2",
    ):
        if part["type"] != "updates":
            continue
        for node, update in (part["data"] or {}).items():
            for m in (
                (update or {}).get("messages", []) if isinstance(update, dict) else []
            ):
                if isinstance(m, ToolMessage):
                    print(
                        part["ns"],
                        node,
                        "ToolMessage",
                        m.name,
                        m.tool_call_id,
                        m.artifact,
                    )


asyncio.run(main(False))
asyncio.run(main(True))
