"""A subagent's pages reach its `task` step, each subagent's its own, with real Deep Agents and
scripted models (subagent_sources.py; explore/subagent_sources/NOTES.md)."""

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from gen9_agent.api.threads import conversation
from gen9_agent.runs.events import EventMapper
from gen9_agent.subagent_sources import SubagentSources

pytestmark = pytest.mark.asyncio


@tool(response_format="content_and_artifact")
async def web_search(query: str) -> tuple[str, list[dict]]:
    """Search the web."""
    return "results", [
        {"url": f"https://{query}.example/{n}", "title": query} for n in range(2)
    ]


class Scripted(BaseChatModel):
    """Answers each call with the next scripted message."""

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


def checker(name: str, query: str) -> dict:
    """A subagent with its own model: searches once for `query`, then answers."""
    model = Scripted(
        script=[
            AIMessage(
                "", tool_calls=[call("web_search", {"query": query}, f"s-{name}")]
            ),
            AIMessage(f"{name} checked"),
        ]
    )
    return {
        "name": name,
        "description": "checks",
        "system_prompt": "check",
        "tools": [web_search],
        "model": model,
        "middleware": [SubagentSources()],
    }


async def test_each_task_step_carries_its_own_subagents_pages() -> None:
    main = Scripted(
        script=[
            AIMessage(
                "",
                tool_calls=[
                    call("task", {"description": "A", "subagent_type": "a"}, "t-a"),
                    call("task", {"description": "B", "subagent_type": "b"}, "t-b"),
                ],
            ),
            AIMessage("both checked"),
        ]
    )
    agent = create_deep_agent(
        model=main,
        subagents=[checker("a", "alpha"), checker("b", "beta")],
        middleware=[SubagentSources()],
    )
    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
    tasks = {
        m.tool_call_id: m
        for m in state["messages"]
        if isinstance(m, ToolMessage) and m.name == "task"
    }
    assert [p["url"] for p in tasks["t-a"].artifact] == [
        "https://alpha.example/0",
        "https://alpha.example/1",
    ]
    assert [p["url"] for p in tasks["t-b"].artifact] == [
        "https://beta.example/0",
        "https://beta.example/1",
    ]
    # The answer's steps after a reload, from the conversation as checkpointed
    steps = {s.id: s for m in conversation(state) for s in m.steps}
    assert [s.url for s in steps["t-a"].sources] == [
        "https://alpha.example/0",
        "https://alpha.example/1",
    ]
    # And live, as the run's events
    [done] = EventMapper().map(
        {"type": "updates", "ns": (), "data": {"tools": {"messages": [tasks["t-b"]]}}}
    )
    assert done.data["sources"][0] == {"url": "https://beta.example/0", "title": "beta"}


async def test_a_task_that_found_nothing_is_left_as_it_was() -> None:
    quiet = Scripted(script=[AIMessage("nothing to search")])
    main = Scripted(
        script=[
            AIMessage(
                "",
                tool_calls=[
                    call("task", {"description": "Q", "subagent_type": "q"}, "t-q")
                ],
            ),
            AIMessage("done"),
        ]
    )
    agent = create_deep_agent(
        model=main,
        subagents=[
            {
                "name": "q",
                "description": "answers",
                "system_prompt": "answer",
                "model": quiet,
                "middleware": [SubagentSources()],
            }
        ],
        middleware=[SubagentSources()],
    )
    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
    [task] = [
        m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "task"
    ]
    assert task.artifact is None
