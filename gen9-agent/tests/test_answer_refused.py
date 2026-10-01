"""An answer sent again is refused and recorded (api/runs.py; manual-e2e.md, P6-C4): a replayed
approval, or an answer to a run no longer asking, answers 409 as before, and the audit record
says who tried, on which run, and why. An answer that goes through records nothing more."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from gen9_agent.api import runs
from gen9_agent.runs import control

pytestmark = pytest.mark.asyncio


async def answer(monkeypatch, outcome: BaseException | None) -> list[dict]:
    recorded: list[dict] = []

    async def fake_answer(*args, **kwargs):
        if outcome is not None:
            raise outcome
        return {"status": "answered"}

    async def record(request, actor, action, *, target=None, outcome=None, detail=None):
        recorded.append(
            {
                "actor": actor,
                "action": action,
                "target": target,
                "outcome": outcome,
                "detail": detail,
            }
        )

    monkeypatch.setattr(runs.control, "answer", fake_answer)
    monkeypatch.setattr(runs.audit, "record", record)
    run = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=None, temporal=None))
    )
    body = SimpleNamespace(
        model_dump=lambda **kwargs: {"decisions": [{"type": "approve"}]}
    )
    try:
        await runs.answer_input(
            run,  # ty: ignore[invalid-argument-type]
            "a1b2",
            body,  # ty: ignore[invalid-argument-type]
            SimpleNamespace(sub="alan"),  # ty: ignore[invalid-argument-type]
            request,  # ty: ignore[invalid-argument-type]
        )
    except HTTPException as e:
        assert e.status_code == 409
    for r in recorded:
        assert r["target"] == run.id
    return recorded


@pytest.mark.parametrize(
    ("refusal", "why"),
    [
        (control.AlreadyAnswered(), "already answered"),
        (control.NotWaiting(), "not waiting"),
    ],
)
async def test_an_answer_refused_is_recorded_with_why(
    monkeypatch, refusal: BaseException, why: str
) -> None:
    [event] = await answer(monkeypatch, refusal)
    assert event["actor"] == "alan" and event["action"] == "run.answer"
    assert event["outcome"] == "denied"
    assert event["detail"] == {"input": "a1b2", "why": why}


async def test_an_answer_that_goes_through_records_nothing_more(monkeypatch) -> None:
    assert await answer(monkeypatch, None) == []
