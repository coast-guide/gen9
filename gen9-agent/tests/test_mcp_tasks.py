"""MCP Tasks for `ask` (mcp_tasks.py): runs as SEP-2663 tasks, what a waiting run asks as
`inputRequests`, and a client's `inputResponses` as the answers `control.answer` takes."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from gen9_agent.mcp_tasks import (
    DECLINED,
    STATUS,
    TTL_MS,
    CreateTaskResult,
    GetTaskResult,
    gen9_answer,
    input_requests,
    tool_result,
    when,
)

pytestmark = pytest.mark.asyncio

QUESTION = {
    "type": "ask_user",
    "questions": [
        {"question": "Which city?", "type": "text", "choices": [], "required": True},
        {
            "question": "How long?",
            "type": "multiple_choice",
            "choices": ["A weekend", "A week"],
            "required": False,
        },
    ],
}
APPROVAL = {
    "action_requests": [
        {"name": "edit_file", "args": {}, "description": "Update your memory"},
        {"name": "execute", "args": {}, "description": "Run a command"},
    ],
    "review_configs": [],
}
ELICITATION = {
    "type": "mcp_elicitation",
    "requests": [
        {
            "key": "trip",
            "mode": "form",
            "message": "Where to?",
            "requested_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        },
        {
            "key": "pay",
            "mode": "url",
            "message": "Pay here",
            "url": "https://pay.example/x",
        },
    ],
}


async def test_a_runs_status_is_a_tasks() -> None:
    assert STATUS["queued"] == STATUS["running"] == "working"
    assert STATUS["waiting"] == "input_required"
    assert STATUS["cancelled"] == "cancelled"
    # Every run that ended otherwise, done or not, completed: `ask`'s result says how
    assert all(
        STATUS.get(s, "completed") == "completed"
        for s in ("success", "error", "expired")
    )


async def test_the_wire_shapes_are_sep_2663s() -> None:
    at = when(datetime(2026, 9, 26, 8, 0, tzinfo=UTC))
    assert at == "2026-09-26T08:00:00Z"
    created = CreateTaskResult(
        task_id="t", status="working", created_at=at, last_updated_at=at
    ).model_dump(exclude_none=True)
    assert created == {
        "resultType": "task",
        "taskId": "t",
        "status": "working",
        "createdAt": at,
        "lastUpdatedAt": at,
        "ttlMs": TTL_MS,
        "pollIntervalMs": 2000,
    }
    got = GetTaskResult(
        task_id="t",
        status="input_required",
        created_at=at,
        last_updated_at=at,
        input_requests={"k": {}},
    ).model_dump(exclude_none=True)
    assert got["resultType"] == "complete" and got["inputRequests"] == {"k": {}}


async def test_questions_become_one_form_and_come_back_as_answers() -> None:
    [(key, request)] = input_requests([("q-1", "question", QUESTION)]).items()
    assert key == "q-1" and request["method"] == "elicitation/create"
    schema = request["params"]["requestedSchema"]
    assert schema["properties"]["q1"] == {"type": "string", "title": "Which city?"}
    assert schema["properties"]["q2"]["enum"] == ["A weekend", "A week"]
    assert schema["required"] == ["q1"]
    accepted = {
        "q-1": {"action": "accept", "content": {"q1": "Lisbon", "q2": "A week"}}
    }
    assert gen9_answer("q-1", "question", QUESTION, accepted) == {
        "answers": ["Lisbon", "A week"]
    }
    declined = {"q-1": {"action": "decline"}}
    assert gen9_answer("q-1", "question", QUESTION, declined) == {
        "answers": [DECLINED, DECLINED]
    }


async def test_an_approval_is_approved_only_where_the_person_said_so() -> None:
    [(_, request)] = input_requests([("a-1", "approval", APPROVAL)]).items()
    props = request["params"]["requestedSchema"]["properties"]
    assert props["a1"]["enum"] == ["approve", "reject"]
    assert props["a1"]["title"] == "Update your memory"
    mixed = {"a-1": {"action": "accept", "content": {"a1": "approve", "a2": "reject"}}}
    assert gen9_answer("a-1", "approval", APPROVAL, mixed) == {
        "decisions": [{"type": "approve"}, {"type": "reject"}]
    }
    # Declining, cancelling or leaving an action out rejects it: nothing runs unapproved
    for response in (
        {"action": "decline"},
        {"action": "cancel"},
        {"action": "accept", "content": {}},
    ):
        assert gen9_answer("a-1", "approval", APPROVAL, {"a-1": response}) == {
            "decisions": [{"type": "reject"}, {"type": "reject"}]
        }


async def test_a_connectors_elicitation_passes_through_and_is_answered_whole() -> None:
    requests = input_requests([("e-1", "elicitation", ELICITATION)])
    assert set(requests) == {"e-1.trip", "e-1.pay"}
    assert requests["e-1.trip"]["params"]["requestedSchema"]["properties"] == {
        "city": {"type": "string"}
    }
    assert requests["e-1.pay"]["params"]["mode"] == "url"
    assert requests["e-1.pay"]["params"]["url"] == "https://pay.example/x"
    trip = {"action": "accept", "content": {"city": "Lisbon"}}
    # One of two answered: nothing sent yet (a connector's round is answered at once)
    assert gen9_answer("e-1", "elicitation", ELICITATION, {"e-1.trip": trip}) is None
    both = {"e-1.trip": trip, "e-1.pay": {"action": "decline"}}
    assert gen9_answer("e-1", "elicitation", ELICITATION, both) == {
        "responses": {"trip": trip, "pay": {"action": "decline"}}
    }


async def test_a_failed_turn_is_tried_again_only_when_asked() -> None:
    [(_, request)] = input_requests(
        [("r-1", "retry", {"error": "The model provider is down."})]
    ).items()
    assert "The model provider is down." in request["params"]["message"]
    yes = {"r-1": {"action": "accept", "content": {"retry": True}}}
    assert gen9_answer("r-1", "retry", {}, yes) == {"retry": True}
    assert gen9_answer("r-1", "retry", {}, {"r-1": {"action": "decline"}}) is None


async def test_unknown_keys_answer_nothing() -> None:
    assert (
        gen9_answer("q-1", "question", QUESTION, {"other": {"action": "accept"}})
        is None
    )


async def test_a_completed_task_carries_asks_own_result() -> None:
    class Answer(BaseModel):
        chat_id: str
        status: str
        answer: str | None

    result = tool_result(Answer(chat_id="c", status="done", answer="ok"))
    assert result["structuredContent"] == {
        "chat_id": "c",
        "status": "done",
        "answer": "ok",
    }
    assert result["isError"] is False and result["content"][0]["type"] == "text"
    # A 2026-07-28 CallToolResult says its type (SEP-2322): ext-tasks' client rejects one without
    assert result["resultType"] == "complete"
