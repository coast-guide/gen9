"""Server-sent events as gen9-agent sends them."""

import httpx2
import pytest

from gen9_cli.main import sse_events

STREAM = (
    'event: run.queued\ndata: {"run_id": "r1"}\nid: 1\n\n'
    'event: status\ndata: {"text": "Searching the web"}\nid: 2\n\n'
    'event: message.delta\ndata: {"id": "m1", "text": "Hel"}\nid: 3\n\n'
    ": ping\n\n"
    'event: message.delta\ndata: {"id": "m1", "text": "lo"}\nid: 4\n\n'
    'event: run.completed\ndata: {"status": "success", "error": null}\nid: 5\n\n'
)


@pytest.mark.asyncio
async def test_parses_events_and_skips_comments():
    response = httpx2.Response(200, stream=httpx2.ByteStream(STREAM.encode()))
    assert [event async for event in sse_events(response)] == [
        ("run.queued", {"run_id": "r1"}, "1"),
        ("status", {"text": "Searching the web"}, "2"),
        ("message.delta", {"id": "m1", "text": "Hel"}, "3"),
        ("message.delta", {"id": "m1", "text": "lo"}, "4"),
        ("run.completed", {"status": "success", "error": None}, "5"),
    ]
