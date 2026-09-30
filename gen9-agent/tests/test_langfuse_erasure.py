"""Erasing a user's Langfuse traces: every page is read, trace IDs deduplicated, deleted in batches."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from gen9_agent.langfuse_erasure import erase_session_traces, erase_user_traces

pytestmark = pytest.mark.asyncio


class FakeLangfuse:
    """Stands in for the SDK's async API: pages of observations, records deletions."""

    def __init__(self, pages: list[list[str]]) -> None:
        self.pages = pages
        self.requests: list[dict] = []
        self.deleted: list[list[str]] = []
        self.observations = SimpleNamespace(get_many=self.get_many)
        self.trace = SimpleNamespace(delete_multiple=self.delete_multiple)

    async def get_many(self, **params):
        self.requests.append(params)
        index = int(params["cursor"] or 0)
        data = [SimpleNamespace(trace_id=t) for t in self.pages[index]]
        more = index + 1 < len(self.pages)
        return SimpleNamespace(
            data=data, meta=SimpleNamespace(cursor=str(index + 1) if more else None)
        )

    async def delete_multiple(self, trace_ids):
        self.deleted.append(list(trace_ids))


SINCE = datetime(2026, 9, 1, tzinfo=UTC)


async def test_reads_every_page_and_deletes_each_trace_once():
    api = FakeLangfuse([["t1", "t2", "t1"], ["t2", "t3", None]])
    assert await erase_user_traces("user-1", SINCE, api=api) == 3
    assert api.deleted == [["t1", "t2", "t3"]]
    assert [r["cursor"] for r in api.requests] == [None, "1"]
    assert {r["user_id"] for r in api.requests} == {"user-1"}
    # The window covers the whole account lifetime, with margin before it
    assert api.requests[0]["from_start_time"] < SINCE < api.requests[0]["to_start_time"]


async def test_deletes_in_batches_of_1000():
    api = FakeLangfuse(
        [[f"t{i}" for i in range(1500)], [f"t{i}" for i in range(1500, 2600)]]
    )
    assert await erase_user_traces("user-1", SINCE, api=api) == 2600
    assert [len(batch) for batch in api.deleted] == [1000, 1000, 600]


async def test_nothing_to_delete():
    api = FakeLangfuse([[]])
    assert await erase_user_traces("user-1", SINCE, api=api) == 0
    assert api.deleted == []


async def test_a_chat_is_erased_by_its_session():
    api = FakeLangfuse([["t1", "t1", "t2"]])
    assert await erase_session_traces("thread-1", SINCE, api=api) == 2
    assert api.deleted == [["t1", "t2"]]
    assert [r.get("session_id") for r in api.requests] == ["thread-1"]
    assert "user_id" not in api.requests[0]
