"""A chat reads the person's memory as it is at each run (memory.FreshMemory), not as it first read
it: Deep Agents' MemoryMiddleware alone keeps the first (manual-e2e.md, P3-E4)."""

import uuid

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import Field

from gen9_agent import memory
from gen9_agent.memory import Gen9Context

pytestmark = pytest.mark.asyncio


class Answers(BaseChatModel):
    """Records each call's system prompt and answers "ok"."""

    systems: list[str] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.systems.append(str(messages[0].content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage("ok"))])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "answers"


def _agent(model: Answers, store: InMemoryStore, fresh: bool):
    return create_deep_agent(
        model=model,
        middleware=[memory.FreshMemory(), memory.MemoryRules()] if fresh else [],
        memory=[memory.MEMORY_PATH],
        backend=CompositeBackend(
            default=StateBackend(), routes={memory.ROUTE: memory.store_backend()}
        ),
        store=store,
        context_schema=Gen9Context,
        checkpointer=InMemorySaver(),
    )


async def _turn(agent, thread: str, context: Gen9Context) -> None:
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Hi"}]},
        {"configurable": {"thread_id": thread}},
        context=context,
    )


async def test_an_edit_and_memory_off_reach_a_chat_already_begun() -> None:
    store, model = InMemoryStore(), Answers()
    agent, thread, alan = _agent(model, store, fresh=True), str(uuid.uuid4()), "alan"
    await memory.write_memory(store, alan, "- Favourite colour: teal.")
    await _turn(agent, thread, Gen9Context(alan))
    assert "teal" in model.systems[-1]

    # Edited in Settings between two messages of the same chat
    await memory.write_memory(store, alan, "- Favourite colour: amber.")
    await _turn(agent, thread, Gen9Context(alan))
    assert "amber" in model.systems[-1] and "teal" not in model.systems[-1]

    # Turned off: the run reads the file saying so, not theirs (the worker writes it first)
    await memory.ensure_memory(store, alan, remember=False)
    await _turn(agent, thread, Gen9Context(alan, remember=False))
    assert "amber" not in model.systems[-1] and "Memory is off" in model.systems[-1]

    # Cleared, then on again: nothing of theirs
    await memory.erase_memory(store, alan)
    await memory.ensure_memory(store, alan, remember=True)
    await _turn(agent, thread, Gen9Context(alan))
    assert "amber" not in model.systems[-1] and "teal" not in model.systems[-1]


async def test_without_it_deep_agents_keeps_what_the_chat_first_read() -> None:
    """Why FreshMemory exists (deepagents #6122). When this fails, Deep Agents reloads on its own,
    and FreshMemory can go."""
    store, model = InMemoryStore(), Answers()
    agent, thread = _agent(model, store, fresh=False), str(uuid.uuid4())
    await memory.write_memory(store, "ada", "- Favourite colour: teal.")
    await _turn(agent, thread, Gen9Context("ada"))
    await memory.write_memory(store, "ada", "- Favourite colour: amber.")
    await _turn(agent, thread, Gen9Context("ada"))
    assert "teal" in model.systems[-1] and "amber" not in model.systems[-1]
