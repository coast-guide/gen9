"""Deleting a chat a restore brought back (threads.delete_thread; gen9-learn plan, M9 T9b): the
request joins a deletion still running for it, whose data steps are done; when the chat is still
there afterwards, a deletion of its own removes it. Seen live: a chat's row put back while its
first deletion waited for late traces, then deleted in the API, is gone."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Response

from gen9_agent.api import threads

pytestmark = pytest.mark.asyncio


class Session:
    """No background tasks; whether the chat's row is still there after the deletion."""

    def __init__(self, still_there: bool) -> None:
        self.still_there = still_there

    async def execute(self, statement: object) -> SimpleNamespace:
        return SimpleNamespace(all=list)

    async def commit(self) -> None:
        pass

    async def scalar(self, statement: object) -> object:
        return uuid.uuid4() if self.still_there else None


async def delete(monkeypatch, still_there: bool) -> list[bool]:
    started: list[bool] = []

    async def start(temporal, thread_id, created_at, sub, *, again=False):
        started.append(again)
        return SimpleNamespace(again=again)

    async def answer(handle, wait_s):
        return Response(status_code=204)

    async def record(*args, **kwargs):
        pass

    monkeypatch.setattr(threads, "start_thread_deletion", start)
    monkeypatch.setattr(threads, "finished_or_accepted", answer)
    monkeypatch.setattr(threads.audit, "record", record)
    thread = SimpleNamespace(
        id=uuid.uuid4(), created_at=datetime.now(UTC), deleted_at=None
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(temporal=None)))
    response = await threads.delete_thread(
        thread,  # ty: ignore[invalid-argument-type]
        SimpleNamespace(sub="alan"),  # ty: ignore[invalid-argument-type]
        Session(still_there),  # ty: ignore[invalid-argument-type]
        request,  # ty: ignore[invalid-argument-type]
    )
    assert response.status_code == 204
    return started


async def test_a_chat_gone_after_its_deletion_is_deleted_once(monkeypatch) -> None:
    assert await delete(monkeypatch, still_there=False) == [False]


async def test_a_chat_still_there_after_the_deletion_it_joined_gets_one_of_its_own(
    monkeypatch,
) -> None:
    assert await delete(monkeypatch, still_there=True) == [False, True]
