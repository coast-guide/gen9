"""Who did what (audit.py): where is the route's template, a record that can't be written doesn't
fail what it records, only another person's real id counts as refused access, and each record is
also one line of JSON in the log, whatever it holds (docs/logging.md, "Sending the logs
elsewhere")."""

import json
import logging
import uuid
from types import SimpleNamespace
from typing import Self

import httpx
import pytest
from fastapi import FastAPI, Request

from gen9_agent import audit, log_safety

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


class Recorded:
    """A session that keeps what was added."""

    def __init__(self) -> None:
        self.added: list = []

    def __call__(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def add(self, row: object) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        return None


def a_request(sessionmaker: object) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(sessionmaker=sessionmaker)),
        scope={},
        method="POST",
        url=SimpleNamespace(path="/v1/connectors"),
    )


def logged(caplog: pytest.LogCaptureFixture) -> list[dict]:
    lines = [r.getMessage() for r in caplog.records if r.name == "gen9_agent.audit"]
    return [
        json.loads(m.removeprefix("audit ")) for m in lines if m.startswith("audit ")
    ]


async def test_each_record_is_also_a_line_of_json_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = Recorded()
    with caplog.at_level(logging.INFO, logger="gen9_agent.audit"):
        await audit.record(
            a_request(session),  # ty: ignore[invalid-argument-type]
            "alan",
            "connector.add",
            target="4c2a",
            detail={"name": "Linear", "host": "mcp.linear.app"},
        )
    assert logged(caplog) == [
        {
            "actor": "alan",
            "action": "connector.add",
            "outcome": "success",
            "target": "4c2a",
            "where": "POST /v1/connectors",
            "detail": {"name": "Linear", "host": "mcp.linear.app"},
        }
    ]
    [row] = session.added
    assert (row.actor, row.action, row.target) == ("alan", "connector.add", "4c2a")


async def test_a_line_stays_one_line_and_valid_json_whatever_a_name_holds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    name = "Zoë\n2026-10-01T00:00:00.000Z INFO: forged\x1b[2J\x7f\x85\u2028end"
    with caplog.at_level(logging.INFO, logger="gen9_agent.audit"):
        await audit.record(
            a_request(Recorded()),  # ty: ignore[invalid-argument-type]
            "alan",
            "connector.add",
            detail={"name": name},
        )
    [record] = [r for r in caplog.records if r.name == "gen9_agent.audit"]
    message = record.getMessage()
    assert not log_safety.CONTROL.search(message)
    assert "Zoë" in message
    [event] = logged(caplog)
    assert event["detail"]["name"] == name


async def test_a_record_the_database_refuses_is_still_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="gen9_agent.audit"):
        await audit.record(
            a_request(Broken()),  # ty: ignore[invalid-argument-type]
            "ada",
            "admin.search.reindex",
        )
    assert [e["action"] for e in logged(caplog)] == ["admin.search.reindex"]
    assert "couldn't record admin.search.reindex by ada" in caplog.text
