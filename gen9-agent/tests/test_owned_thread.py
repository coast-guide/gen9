"""A chat's owner check gives its connection back at once (threads.owned_thread; P4-E3): the
request's session lives until its response is sent, a stream's until the stream ends, and a
read's open transaction held a pooled connection all that time. Twelve open streams took ten
connections, the whole pool, and every other request waited 25 s."""

import uuid
from types import SimpleNamespace

import pytest

from gen9_agent.api.threads import owned_thread

pytestmark = pytest.mark.asyncio


class Session:
    def __init__(self, thread: object) -> None:
        self.thread = thread
        self.calls: list[str] = []

    async def scalar(self, statement: object) -> object:
        self.calls.append("read")
        return self.thread

    async def commit(self) -> None:
        self.calls.append("commit")


async def test_the_read_ends_its_transaction_before_the_route_goes_on() -> None:
    thread = SimpleNamespace(id=uuid.uuid4(), title="kept")
    session = Session(thread)
    user = SimpleNamespace(id=uuid.uuid4())
    found = await owned_thread(thread.id, user, session, SimpleNamespace())  # ty: ignore[invalid-argument-type]
    assert found is thread and found.title == "kept"
    assert session.calls == ["read", "commit"]
