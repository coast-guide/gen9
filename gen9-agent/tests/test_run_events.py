"""The run events a client sees, from the parts of the agent's LangGraph v2 stream."""

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.types import Overwrite

from gen9_agent.api.runs import _resume_point
from gen9_agent.runs.events import MAX_TOOL_OUTPUT, EventMapper, RunEvent

MODEL = {"langgraph_node": "model"}


def chunk(text="", content=None, id="m1"):
    return {
        "type": "messages",
        "ns": (),
        "data": (AIMessageChunk(content=content or text, id=id), MODEL),
    }


def test_answer_text_streams_as_deltas():
    mapper = EventMapper()
    assert mapper.map(chunk("Can")) == [
        RunEvent("message.delta", {"id": "m1", "text": "Can"})
    ]
    assert mapper.map(chunk("")) == []


def test_web_search_is_announced_once_per_message():
    mapper = EventMapper()
    search = [{"type": "web_search_call", "id": "ws1"}]
    assert mapper.map(chunk(content=search)) == [
        RunEvent("status", {"text": "Searching the web"})
    ]
    assert mapper.map(chunk(content=search)) == []
    assert mapper.map(chunk(content=search, id="m2")) == [
        RunEvent("status", {"text": "Searching the web"})
    ]


def test_only_the_model_node_speaks():
    tool_chunk = {
        "type": "messages",
        "ns": (),
        "data": (AIMessageChunk(content="x"), {"langgraph_node": "tools"}),
    }
    assert EventMapper().map(tool_chunk) == []


def test_subagent_parts_are_left_out():
    part = chunk("inner")
    part["ns"] = ("tools:abc",)
    assert EventMapper().map(part) == []


def test_completed_message_and_tool_calls_from_updates():
    ai = AIMessage(
        content="Let me look.",
        id="m1",
        tool_calls=[{"id": "c1", "name": "lookup", "args": {"city": "Paris"}}],
    )
    events = EventMapper().map(
        {"type": "updates", "ns": (), "data": {"model": {"messages": [ai]}}}
    )
    assert events == [
        RunEvent("message.completed", {"id": "m1", "text": "Let me look."}),
        RunEvent(
            "tool.started", {"id": "c1", "name": "lookup", "args": {"city": "Paris"}}
        ),
    ]


def test_tool_results_are_truncated_and_carry_their_status():
    long = ToolMessage(
        content="x" * (MAX_TOOL_OUTPUT + 10), tool_call_id="c1", name="lookup"
    )
    failed = ToolMessage(
        content="boom", tool_call_id="c2", name="lookup", status="error"
    )
    [done, error] = EventMapper().map(
        {
            "type": "updates",
            "ns": (),
            "data": {"tools": {"messages": Overwrite([long, failed])}},
        }
    )
    assert done.type == "tool.completed" and done.data["status"] == "success"
    assert len(done.data["output"]) == MAX_TOOL_OUTPUT + 1 and done.data[
        "output"
    ].endswith("…")
    assert error.data == {
        "id": "c2",
        "name": "lookup",
        "status": "error",
        "output": "boom",
    }


def test_a_call_the_person_denied_is_declined_not_failed():
    denied = ToolMessage(
        content="User rejected the tool call for `edit_file` with reason: not now",
        tool_call_id="c1",
        name="edit_file",
        status="error",
    )
    [event] = EventMapper().map(
        {"type": "updates", "ns": (), "data": {"tools": {"messages": [denied]}}}
    )
    assert event.data["status"] == "declined"
    assert event.data["output"].endswith("with reason: not now")


def test_plan_changes():
    todos = [{"content": "Look up Paris", "status": "in_progress"}]
    assert EventMapper().map(
        {"type": "updates", "ns": (), "data": {"model": {"todos": todos}}}
    ) == [RunEvent("todos.updated", {"todos": todos})]


def test_updates_without_messages_are_ignored():
    assert (
        EventMapper().map(
            {
                "type": "updates",
                "ns": (),
                "data": {"PatchToolCallsMiddleware.before_agent": None},
            }
        )
        == []
    )


def test_resume_point_prefers_last_event_id():
    assert _resume_point("7", 3) == 7
    assert _resume_point(None, 3) == 3
    assert _resume_point("not-a-number", None) == 0
    assert _resume_point(None, None) == 0


def test_live_steps_and_answers_carry_their_sources():
    ai = AIMessage(
        id="m7",
        content=[
            {
                "type": "web_search_call",
                "id": "ws7",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "valkey",
                    "sources": [{"type": "url", "url": "https://valkey.io/"}],
                },
            },
            {
                "type": "text",
                "text": "9.1.2",
                "annotations": [
                    {
                        "type": "url_citation",
                        "url": "https://valkey.io/",
                        "title": "Valkey",
                    }
                ],
            },
        ],
    )
    router = ToolMessage(
        content="[]",
        artifact=[{"title": "GitHub", "url": "https://github.com/valkey-io/valkey"}],
        tool_call_id="r1",
        name="web_search",
    )
    events = EventMapper().map(
        {"type": "updates", "ns": (), "data": {"model": {"messages": [ai, router]}}}
    )
    completed = [e.data for e in events if e.type == "tool.completed"]
    assert completed[0]["sources"] == [{"url": "https://valkey.io/"}]
    assert completed[1]["sources"] == [
        {"url": "https://github.com/valkey-io/valkey", "title": "GitHub"}
    ]
    [message] = [e.data for e in events if e.type == "message.completed"]
    assert message["citations"] == [{"url": "https://valkey.io/", "title": "Valkey"}]


def test_a_run_waiting_for_retry_says_why_in_plain_words():
    from gen9_agent.runs.store import (
        PUBLIC_BUDGET_ERROR,
        PUBLIC_NO_ANSWER,
        PUBLIC_NO_CREDITS,
        retry_reason,
    )

    credits = "APIError: litellm.APIError: You have no credits remaining. Add credits to continue"
    assert retry_reason(credits) == PUBLIC_NO_CREDITS
    assert (
        retry_reason("RateLimitError: Error code: 429 - insufficient_quota")
        == PUBLIC_NO_CREDITS
    )
    assert (
        retry_reason("BudgetExceeded: budget_exceeded for user") == PUBLIC_BUDGET_ERROR
    )
    assert (
        retry_reason("ConnectError: [Errno 111] Connection refused") == PUBLIC_NO_ANSWER
    )


def test_summarizing_earlier_messages_is_said_once_not_shown_as_the_answer() -> None:
    mapper = EventMapper()
    summary = {"langgraph_node": "model", "lc_source": "summarization"}
    events = [
        *mapper.map(
            {
                "type": "messages",
                "data": (AIMessageChunk(content="The user said"), summary),
            }
        ),
        *mapper.map(
            {
                "type": "messages",
                "data": (AIMessageChunk(content=" three things."), summary),
            }
        ),
        *mapper.map(
            {
                "type": "messages",
                "data": (
                    AIMessageChunk(content="Hello", id="m1"),
                    {"langgraph_node": "model"},
                ),
            }
        ),
    ]
    assert [e.type for e in events] == ["context.summarized", "message.delta"]
    assert events[1].data == {"id": "m1", "text": "Hello"}
