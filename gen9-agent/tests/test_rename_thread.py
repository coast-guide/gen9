"""Renaming a chat (threads.rename_thread; gen9-learn.md, M9, F5): one line of 1 to 80
characters, its whitespace collapsed; the owner check is owned_thread's (test_owned_thread.py),
and e2e/stacks.mjs renames a chat in the web app."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gen9_agent.api.threads import ThreadPatch, one_line, rename_thread

pytestmark = pytest.mark.asyncio


class Session:
    def __init__(self, run_status: str | None) -> None:
        self.run_status = run_status
        self.commits = 0

    async def scalar(self, statement: object) -> object:
        return self.run_status

    async def commit(self) -> None:
        self.commits += 1


def a_thread() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="What does RFC 10017 recommend for browser apps?",
        created_at=now,
        updated_at=now,
        permission_mode="auto",
        parent_id=None,
    )


async def test_a_name_is_one_line_with_single_spaces() -> None:
    assert one_line("  Browser\napps,\t RFC 10017  ") == "Browser apps, RFC 10017"
    assert one_line(" \n\t ") == ""


async def test_a_name_is_1_to_80_characters() -> None:
    assert ThreadPatch(title="x" * 80).title == "x" * 80
    for title in ("", "x" * 81):
        with pytest.raises(ValidationError):
            ThreadPatch(title=title)


async def test_renaming_saves_the_name_and_keeps_the_chats_place() -> None:
    thread, session = a_thread(), Session("running")
    updated = thread.updated_at
    out = await rename_thread(ThreadPatch(title=" Browser  apps "), thread, session)  # ty: ignore[invalid-argument-type]
    assert out.title == thread.title == "Browser apps"
    assert out.run_status == "running" and session.commits == 1
    assert thread.updated_at == updated


async def test_a_blank_name_is_refused_and_nothing_is_saved() -> None:
    thread, session = a_thread(), Session(None)
    before = thread.title
    with pytest.raises(HTTPException) as refused:
        await rename_thread(ThreadPatch(title="  \n "), thread, session)  # ty: ignore[invalid-argument-type]
    assert refused.value.status_code == 422 and refused.value.detail == "Name the chat."
    assert thread.title == before and session.commits == 0
