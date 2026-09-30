"""Grounding (grounding.py): every model call knows today's date, a turn's searches are bounded,
after which the model is told to answer, and so are its model calls, after which the turn ends
with a plain answer. Subagents get the same (agent.subagents)."""

import uuid
from datetime import UTC, datetime

import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from gen9_agent.agent import subagents
from gen9_agent.definition import GEN9, AgentDefinition, SubAgentSpec
from gen9_agent.grounding import (
    MODEL_CALLS_PER_TURN,
    MODEL_CALLS_PER_TURN_ALL,
    SEARCHES_PER_TURN,
    STOPPED,
    STOPPED_ALL,
    StepBudget,
    TodaysDate,
    TurnBudget,
    middleware,
    search_budget,
    with_note,
)
from gen9_agent.memory import Gen9Context, MemoryRules

pytestmark = pytest.mark.asyncio


async def test_a_note_joins_text_and_block_system_messages() -> None:
    assert (
        with_note(SystemMessage(content="Be brief."), "Today.").content
        == "Be brief.\n\nToday."
    )
    blocks = with_note(
        SystemMessage(content=[{"type": "text", "text": "Be brief."}]), "Today."
    )
    assert blocks.content[-1] == {"type": "text", "text": "Today."}
    assert with_note(None, "Today.").content == "Today."


SEARCHES: list[str] = []


@tool
async def web_search(query: str) -> str:
    """Search the web."""
    SEARCHES.append(query)
    return f"results for {query}"


def said(message: AIMessage) -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=message)])


class Scripted(BaseChatModel):
    """Searches on every call, forever, unless told not to; records what it was told. With
    `delegate`, the main agent hands the question to the general-purpose subagent first."""

    calls: int = 0
    systems: list[str] = Field(default_factory=list)
    delegate: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        self.systems.append(str(messages[0].content) if messages else "")
        last = messages[-1]
        if (
            self.delegate
            and isinstance(last, HumanMessage)
            and last.text == "latest postgres?"
        ):
            task = {
                "name": "task",
                "args": {
                    "description": "Find the latest PostgreSQL release.",
                    "subagent_type": "general-purpose",
                },
                "id": "t1",
                "type": "tool_call",
            }
            return said(AIMessage("", tool_calls=[task]))
        if isinstance(messages[-1], ToolMessage) and messages[-1].name == "task":
            return said(AIMessage(f"It found: {messages[-1].text}"))
        refused = any(
            isinstance(m, ToolMessage) and "Do not call 'web_search' again" in m.text
            for m in messages
        )
        if refused:
            return said(AIMessage("With what I found: 18.6."))
        call = {
            "name": "web_search",
            "args": {"query": f"q{self.calls}"},
            "id": f"c{self.calls}",
            "type": "tool_call",
        }
        return said(AIMessage("", tool_calls=[call]))

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


async def test_the_model_knows_the_date_and_stops_searching_at_the_budget() -> None:
    SEARCHES.clear()
    model = Scripted()
    agent = create_deep_agent(
        model=model,
        tools=[web_search],
        checkpointer=InMemorySaver(),
        middleware=[TodaysDate(), search_budget()],
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "latest postgres?"}]},
        {"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    assert len(SEARCHES) == SEARCHES_PER_TURN
    assert result["messages"][-1].text == "With what I found: 18.6."
    today = datetime.now(UTC).strftime("%d %B %Y")
    assert all(today in system for system in model.systems)


def definition(*subs: SubAgentSpec) -> AgentDefinition:
    return AgentDefinition(
        name="gen9",
        description="",
        model="chat",
        instructions="Be brief.",
        skills_dir=GEN9 / "skills",
        skills=(),
        subagents=subs,
        version="0",
    )


RESEARCHER = SubAgentSpec(
    name="researcher", description="Researches.", instructions="Research.", model=None
)


async def test_every_subagent_is_grounded_and_a_definition_may_declare_its_own_general() -> (
    None
):
    specs = subagents(definition(RESEARCHER), lambda alias: Scripted())
    assert [s["name"] for s in specs] == ["general-purpose", "researcher"]
    for spec in specs:
        kinds = {type(m) for m in spec.get("middleware", [])}
        # Grounded, with its budgets and the turn's, and bound by what may be remembered
        assert kinds == {
            TodaysDate,
            ToolCallLimitMiddleware,
            StepBudget,
            TurnBudget,
            MemoryRules,
        }
    # The person's plugins' skills, then Gen9's own (plugin_skills.py)
    assert specs[0]["skills"] == ["/plugins/", "/skills/"]
    own = SubAgentSpec(
        name="general-purpose", description="Mine.", instructions="Mine.", model=None
    )
    assert [
        s["description"] for s in subagents(definition(own), lambda _: Scripted())
    ] == ["Mine."]


async def test_a_delegated_search_knows_the_date_and_stops_at_the_budget() -> None:
    SEARCHES.clear()
    model = Scripted(delegate=True)
    agent = create_deep_agent(
        model=model,
        tools=[web_search],
        checkpointer=InMemorySaver(),
        subagents=subagents(definition(), lambda alias: model),
        middleware=[TodaysDate(), search_budget()],
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "latest postgres?"}]},
        {"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    assert len(SEARCHES) == SEARCHES_PER_TURN
    assert result["messages"][-1].text == "It found: With what I found: 18.6."
    today = datetime.now(UTC).strftime("%d %B %Y")
    assert all(today in system for system in model.systems)


LOOKUPS: list[str] = []


@tool
async def look_up(thing: str) -> str:
    """Look something up."""
    LOOKUPS.append(thing)
    return f"more about {thing}"


class Looping(BaseChatModel):
    """Calls a tool on every call and never answers: a model stuck in a loop."""

    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        call = {
            "name": "look_up",
            "args": {"thing": f"t{self.calls}"},
            "id": f"l{self.calls}",
            "type": "tool_call",
        }
        return said(AIMessage("", tool_calls=[call]))

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "looping"


async def test_a_looping_turn_ends_at_the_step_budget_with_a_plain_answer() -> None:
    LOOKUPS.clear()
    model = Looping()
    agent = create_deep_agent(
        model=model,
        tools=[look_up],
        checkpointer=InMemorySaver(),
        middleware=middleware(),
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "find everything"}]}, config
    )
    assert model.calls == MODEL_CALLS_PER_TURN
    assert len(LOOKUPS) == MODEL_CALLS_PER_TURN
    assert result["messages"][-1].text == STOPPED
    # The next turn in the same chat gets its own budget
    await agent.ainvoke({"messages": [{"role": "user", "content": "go on"}]}, config)
    assert model.calls == 2 * MODEL_CALLS_PER_TURN


async def test_the_step_budget_counts_each_turn() -> None:
    assert StepBudget().run_limit == MODEL_CALLS_PER_TURN
    assert StepBudget().thread_limit is None
    assert any(isinstance(m, StepBudget) for m in middleware())


class Delegating(BaseChatModel):
    """Hands the work to a subagent on every call and never answers."""

    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        call = {
            "name": "task",
            "args": {"description": "find more", "subagent_type": "general-purpose"},
            "id": f"d{self.calls}",
            "type": "tool_call",
        }
        return said(AIMessage("", tool_calls=[call]))

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "delegating"


async def test_a_turn_that_keeps_delegating_ends_at_the_turns_budget() -> None:
    # P5-C8: each agent counts its own 50; together they stop at the turn's
    LOOKUPS.clear()
    main, helper = Delegating(), Looping()
    agent = create_deep_agent(
        model=main,
        tools=[look_up],
        checkpointer=InMemorySaver(),
        subagents=[
            {
                "name": "general-purpose",
                "description": "Helps.",
                "system_prompt": "Help.",
                "model": helper,
                "tools": [look_up],
                "middleware": middleware(),
            }
        ],
        middleware=middleware(),
        context_schema=Gen9Context,
    )
    context = Gen9Context("alan")
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "find everything"}]},
        {"configurable": {"thread_id": str(uuid.uuid4()), "recursion_limit": 2000}},
        context=context,
    )
    assert context.turn.calls == MODEL_CALLS_PER_TURN_ALL
    assert main.calls + helper.calls == MODEL_CALLS_PER_TURN_ALL
    assert result["messages"][-1].text == STOPPED_ALL
