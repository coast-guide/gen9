"""A task that asks again after an answer (a connector's server asking twice in one tool call) keeps
its LangGraph interrupt id, so each round gets a request id of its own and the answer resumes the
interrupt by its own id (M9, 4c-4). Real LangGraph graphs, an in-memory checkpointer."""

import uuid
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph._internal._constants import RESUME
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from gen9_agent.runs import executor, store

pytestmark = pytest.mark.asyncio


class S(TypedDict, total=False):
    answers: list[str]


def ask_three(state: S) -> S:
    return {"answers": [interrupt({"q": n}) for n in (1, 2, 3)]}


def graph(node) -> tuple[object, InMemorySaver]:
    g = StateGraph(S)
    g.add_node("ask", node)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    saver = InMemorySaver()
    return g.compile(checkpointer=saver), saver


async def test_the_resume_channel_is_langgraphs() -> None:
    assert executor.RESUME_WRITES == RESUME


async def walk(app, saver) -> tuple[list[tuple[str, str | None]], dict]:
    """Answer each round through the executor's own mapping: request ids as it gives them, the
    resume by interrupt id. Returns (request id, interrupt id) per round, and the answers."""
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": "t"}, "metadata": {"run_id": run_id}}
    runtime = SimpleNamespace(agent=app, checkpointer=saver)
    run = SimpleNamespace(id=run_id)
    await app.ainvoke({}, config)
    seen = []
    for answer in ("A", "B", "C"):
        pending = await executor.paused(runtime, run, config)  # ty: ignore[invalid-argument-type]
        assert len(pending) == 1
        p = pending[0]
        seen.append((p.id, p.interrupt_id))
        await app.ainvoke(Command(resume={p.interrupt_id or p.id: answer}), config)
    assert await executor.paused(runtime, run, config) == []  # ty: ignore[invalid-argument-type]
    return seen, (await app.aget_state(config)).values


async def test_each_round_of_one_task_gets_its_own_request_id() -> None:
    app, saver = graph(ask_three)
    seen, values = await walk(app, saver)
    first = seen[0][1]
    assert [s[1] for s in seen] == [first] * 3  # LangGraph's id, the same every round
    assert [s[0] for s in seen] == [first, f"{first}-2", f"{first}-3"]
    assert values["answers"] == ["A", "B", "C"]  # each answer reached its own question


async def test_a_subagents_rounds_are_counted_in_its_own_namespace() -> None:
    inner, _ = graph(ask_three)
    outer = StateGraph(S)
    outer.add_node("helper", inner)
    outer.add_edge(START, "helper")
    outer.add_edge("helper", END)
    saver = InMemorySaver()
    app = outer.compile(checkpointer=saver)
    seen, values = await walk(app, saver)
    first = seen[0][1]
    assert [s[0] for s in seen] == [first, f"{first}-2", f"{first}-3"]
    assert values["answers"] == ["A", "B", "C"]


async def test_round_ids() -> None:
    assert store.round_id("abc", 1) == "abc"
    assert store.round_id("abc", 2) == "abc-2"
