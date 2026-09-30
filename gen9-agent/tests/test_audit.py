"""Who did what (audit.py): where is the route's template, a record that can't be written doesn't
fail what it records, and only another person's real id counts as refused access."""

import uuid
from types import SimpleNamespace
from typing import Self

import httpx
import pytest
from fastapi import FastAPI, Request

from gen9_agent import audit

pytestmark = pytest.mark.asyncio


async def test_where_is_the_routes_template_not_the_url() -> None:
    app = FastAPI()

    @app.patch("/v1/admin/users/{user_id}")
    async def patch(user_id: str, request: Request) -> str:
        return audit.where(request)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        said = (await client.patch("/v1/admin/users/abc%0Ainjected")).json()
    assert said == "PATCH /v1/admin/users/{user_id}"


class Broken:
    def __call__(self) -> "Broken":
        return self

    async def __aenter__(self) -> Self:
        raise RuntimeError("the database is down")

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_a_record_that_cant_be_written_doesnt_fail_the_action(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(sessionmaker=Broken())),
        scope={},
        method="POST",
        url=SimpleNamespace(path="/v1/x"),
    )
    await audit.record(request, "ada", "admin.search.reindex")  # ty: ignore[invalid-argument-type]
    assert "couldn't record admin.search.reindex by ada" in caplog.text


class Session:
    def __init__(self, owner: uuid.UUID | None) -> None:
        self.owner = owner

    async def scalar(self, _query: object) -> uuid.UUID | None:
        return self.owner


async def test_only_another_persons_real_id_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple] = []

    async def record(request: object, actor: str, action: str, **kw: object) -> None:
        recorded.append((actor, action, kw["target"], kw["outcome"]))

    monkeypatch.setattr(audit, "record", record)
    me, them, thing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=me, sub="alan")
    from gen9_agent.models import Thread

    for owner in (None, me):
        await audit.theirs(None, Session(owner), Thread, thing, user, "thread")  # ty: ignore[invalid-argument-type]
    assert recorded == []
    await audit.theirs(None, Session(them), Thread, thing, user, "thread")  # ty: ignore[invalid-argument-type]
    assert recorded == [("alan", "thread.access", thing, audit.DENIED)]


async def test_another_persons_run_under_ones_own_chat_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run or file belongs to its chat's owner: Ada's run tried under Alan's own chat is
    refused with 404 and recorded (P2-A1), a missing one or his own isn't."""
    recorded: list[tuple] = []

    async def record(request: object, actor: str, action: str, **kw: object) -> None:
        recorded.append((actor, action, kw["target"], kw["outcome"]))

    monkeypatch.setattr(audit, "record", record)
    me, them, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=me, sub="alan")
    from sqlalchemy import select

    from gen9_agent.models import Run, Thread

    owner = (
        select(Thread.user_id)
        .join(Run, Run.thread_id == Thread.id)
        .where(Run.id == run)
    )
    for found in (None, me):
        await audit.theirs_through(None, Session(found), owner, run, user, "run")  # ty: ignore[invalid-argument-type]
    assert recorded == []
    await audit.theirs_through(None, Session(them), owner, run, user, "run")  # ty: ignore[invalid-argument-type]
    assert recorded == [("alan", "run.access", run, audit.DENIED)]
