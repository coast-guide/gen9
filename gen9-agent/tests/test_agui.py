"""Gen9 over AG-UI (api/agui.py): Gen9's run events as AG-UI events, a request as an interrupt,
and how a run's end is said. The endpoint on the real stacks is e2e's (`e2e/agui.mjs`)."""

import uuid
from types import SimpleNamespace

import pytest
from ag_ui.core import (
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
)

from gen9_agent.api import agui
from gen9_agent.api.agui import ANSWERS, Translation, end, interrupt

pytestmark = pytest.mark.asyncio


def _types(events) -> list[str]:
    return [e.type.value for e in events]


async def test_a_streamed_answer_is_one_text_message() -> None:
    t = Translation()
    events = [
        *t.events("message.delta", {"id": "m1", "text": "Hel"}),
        *t.events("message.delta", {"id": "m1", "text": "lo"}),
        *t.events("message.completed", {"id": "m1", "text": "Hello"}),
    ]
    assert _types(events) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert "".join(e.delta for e in events if hasattr(e, "delta")) == "Hello"


async def test_a_whole_message_is_written_at_once() -> None:
    events = Translation().events("message.completed", {"id": "m2", "text": "Done."})
    assert _types(events) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]


async def test_a_tool_call_closes_the_text_and_carries_its_result() -> None:
    t = Translation()
    t.events("message.delta", {"id": "m1", "text": "Let me look."})
    started = t.events(
        "tool.started", {"id": "c1", "name": "web_search", "args": {"query": "tides"}}
    )
    finished = t.events(
        "tool.completed", {"id": "c1", "name": "web_search", "status": "success"}
    )
    assert _types(started) == [
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert started[1].tool_call_name == "web_search"
    assert started[2].delta == '{"query": "tides"}'
    assert finished[0].tool_call_id == "c1" and finished[0].content == "success"


async def test_the_plan_is_state() -> None:
    [snapshot] = Translation().events(
        "todos.updated", {"todos": [{"content": "Read", "status": "pending"}]}
    )
    assert snapshot.snapshot == {"todos": [{"content": "Read", "status": "pending"}]}


async def test_a_request_is_an_interrupt_with_the_answer_it_takes() -> None:
    asked = interrupt(
        {
            "id": "q1",
            "kind": "question",
            "questions": [{"question": "Which city?", "type": "text"}],
        }
    )
    assert (asked.id, asked.reason, asked.message) == ("q1", "question", "Which city?")
    assert asked.response_schema == ANSWERS["question"]
    assert asked.metadata == {
        "gen9": {"questions": [{"question": "Which city?", "type": "text"}]}
    }
    approval = interrupt({"id": "a1", "kind": "approval", "action_requests": []})
    assert approval.response_schema["required"] == ["decisions"]


async def test_how_a_run_ends() -> None:
    done = end({"status": "success"}, "t", "r")
    assert isinstance(done, RunFinishedEvent)
    assert isinstance(done.outcome, RunFinishedSuccessOutcome)
    stopped = end({"status": "cancelled"}, "t", "r")
    assert isinstance(stopped, RunFinishedEvent)
    assert isinstance(stopped.outcome, RunFinishedCancelledOutcome)
    failed = end({"status": "error", "error": "The model provider is down."}, "t", "r")
    assert isinstance(failed, RunErrorEvent)
    assert failed.message == "The model provider is down." and failed.code == "error"


async def ended(monkeypatch, stopping: bool) -> RunFinishedEvent:
    """How `follow` ends for a run that still reads as waiting when the stream starts, its
    `run.completed` (cancelled) written a moment later, as after a resume that stops it."""
    reads = iter([[], [(7, "run.completed", {"status": "cancelled", "error": None})]])

    async def read_after(engine, run_id, after):
        return next(reads)

    async def waiting_on(engine, run_id):
        return [{"id": "a1", "kind": "approval", "action_requests": []}]

    class Hub:
        async def wait(self, run_id):
            return None

    async def connected() -> bool:
        return False

    monkeypatch.setattr(agui.log, "read_after", read_after)
    monkeypatch.setattr(agui, "waiting_on", waiting_on)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=None, event_hub=Hub())),
        is_disconnected=connected,
    )
    events = [
        e async for e in agui.follow(request, uuid.uuid4(), 6, "t", "r", stopping)
    ]
    return events[-1]


async def test_a_resume_that_stops_the_run_ends_as_cancelled(monkeypatch) -> None:
    # Before the stop is recorded the run still waits: without `stopping` the client would get
    # back the interrupt it just cancelled
    assert isinstance(
        (await ended(monkeypatch, False)).outcome, RunFinishedInterruptOutcome
    )
    assert isinstance(
        (await ended(monkeypatch, True)).outcome, RunFinishedCancelledOutcome
    )
