"""A notice the database dropped for a moment is tried again (notices.notify_safely; P6-B3): one was
lost to a single ended connection, the run itself fine."""

import uuid

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError

from gen9_agent import notices

pytestmark = pytest.mark.asyncio


def failing(errors: list[Exception]):
    calls: list[int] = []

    async def notify(runtime, run_id, kind, graded=False) -> bool:
        calls.append(1)
        if errors:
            raise errors.pop(0)
        return True

    return notify, calls


def dropped() -> DBAPIError:
    return DBAPIError(
        "select …", {}, psycopg.errors.AdminShutdown("terminating connection")
    )


async def test_a_moment_without_the_database_is_tried_again(monkeypatch) -> None:
    notify, calls = failing([dropped()])
    monkeypatch.setattr(notices, "notify", notify)
    monkeypatch.setattr(notices, "NOTICE_BACKOFF_S", 0)
    await notices.notify_safely(None, uuid.uuid4(), "done")  # ty: ignore[invalid-argument-type]
    assert len(calls) == 2


async def test_it_gives_up_after_its_tries_and_never_fails_the_run(monkeypatch) -> None:
    notify, calls = failing([dropped() for _ in range(5)])
    monkeypatch.setattr(notices, "notify", notify)
    monkeypatch.setattr(notices, "NOTICE_BACKOFF_S", 0)
    await notices.notify_safely(None, uuid.uuid4(), "done")  # ty: ignore[invalid-argument-type]
    assert len(calls) == notices.NOTICE_TRIES


async def test_any_other_failure_is_tried_once(monkeypatch) -> None:
    other = DBAPIError("…", {}, psycopg.errors.UniqueViolation("duplicate key"))
    notify, calls = failing([other, OSError("smtp down")])
    monkeypatch.setattr(notices, "notify", notify)
    await notices.notify_safely(None, uuid.uuid4(), "done")  # ty: ignore[invalid-argument-type]
    notify2, calls2 = failing([OSError("smtp down")])
    monkeypatch.setattr(notices, "notify", notify2)
    await notices.notify_safely(None, uuid.uuid4(), "done")  # ty: ignore[invalid-argument-type]
    assert len(calls) == 1 and len(calls2) == 1
