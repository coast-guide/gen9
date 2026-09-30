"""A background run's "done" or "waiting" email survives its worker stopping between recording the
run and sending it (M9, F23): the repeated attempt sends it, and a notice is sent once
(notices.notify, keyed by run and kind). The run itself is faked; the executor's own code runs."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from gen9_agent.runs import executor, store
from gen9_agent.runs.store import Pending, StartedRun

pytestmark = pytest.mark.asyncio


@pytest.fixture
def told(monkeypatch) -> list[str]:
    sent: list[str] = []

    async def notify_safely(runtime, run_id, kind, graded=False) -> None:
        sent.append(kind)

    monkeypatch.setattr(executor.notices, "notify_safely", notify_safely)
    return sent


@pytest.mark.parametrize(
    ("status", "notices"), [("success", ["done"]), ("error", []), ("cancelled", [])]
)
async def test_a_repeated_attempt_of_a_finished_turn_sends_its_done_notice(
    monkeypatch, told, status, notices
) -> None:
    async def start(*args):
        return None  # the run already ended: the attempt before recorded it

    async def run_status(engine, run_id):
        return status

    monkeypatch.setattr(store, "start", start)
    monkeypatch.setattr(store, "status", run_status)
    runtime = SimpleNamespace(engine=None, definition=SimpleNamespace(version="v"))
    result = await executor.execute(runtime, uuid.uuid4(), 2)  # ty: ignore[invalid-argument-type]
    assert result == {"status": "ended", "pending": []}
    # A failed run's notice is finish_run's, never this path's
    assert told == notices


async def test_a_repeated_attempt_of_a_paused_turn_sends_its_waiting_notice(
    monkeypatch, told
) -> None:
    run = StartedRun(
        id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        input={"message": "Every morning, sum up my inbox"},
        started_at=None,
        user_sub="sub",
        parent_id=None,
        search_past_chats=True,
        remember=True,
    )
    waiting_for = [Pending("i1", "approval", {"action_requests": []})]

    async def start(*args):
        return run, 7

    async def nothing(*args, **kwargs):
        return None

    async def paused(*args):
        return waiting_for

    async def responses(conn, run_id, ids):
        return None  # the person hasn't answered: the turn keeps waiting

    async def wait(self, started, pending):
        return {"status": "waiting", "pending": [p.id for p in pending]}

    async def skills(*args):
        return SimpleNamespace(plugin_of={})

    @asynccontextmanager
    async def opened():
        yield None

    monkeypatch.setattr(store, "start", start)
    monkeypatch.setattr(store, "responses", responses)
    monkeypatch.setattr(executor, "paused", paused)
    monkeypatch.setattr(executor._Writer, "wait", wait)
    monkeypatch.setattr(executor.plugin_connectors, "reconcile", nothing)
    monkeypatch.setattr(executor.plugin_skills, "load", skills)
    monkeypatch.setattr(executor.memory, "ensure_memory", nothing)
    monkeypatch.setattr(executor, "langfuse_callbacks", list)

    async def active(sub):
        return True

    runtime = SimpleNamespace(
        engine=SimpleNamespace(connect=opened),
        sessionmaker=opened,
        definition=SimpleNamespace(version="v", skill_names=[]),
        standing=SimpleNamespace(active=active),
        store=None,
    )
    result = await executor.execute(runtime, run.id, 2)  # ty: ignore[invalid-argument-type]
    assert result == {"status": "waiting", "pending": ["i1"]}
    assert told == ["waiting"]
