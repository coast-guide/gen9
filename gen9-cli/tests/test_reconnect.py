"""`gen9 ask` when the connection drops mid-answer (found by hand: manual-e2e.md, P2-I4): it
reconnects to the run's events and goes on after the last one it showed (Last-Event-ID), through
an API that is still starting (5xx); after RECONNECTS failures in a row it says the answer goes
on, and where to find it."""

import asyncio

import httpx2
import pytest

from gen9_cli import main
from gen9_cli.main import parse

pytestmark = pytest.mark.asyncio


def event(name: str, data: str, event_id: int) -> bytes:
    return f"event: {name}\ndata: {data}\nid: {event_id}\n\n".encode()


async def dropped():
    yield event("run.queued", '{"run_id": "r1"}', 1)
    yield event("message.delta", '{"id": "m1", "text": "Hel"}', 2)
    raise httpx2.RemoteProtocolError("peer closed connection")


async def rest():
    yield event("message.delta", '{"id": "m1", "text": "lo"}', 3)
    yield event("run.completed", '{"status": "success", "error": null}', 4)


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def token(_keycloak) -> str:
        return "token"

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(main, "access_token", token)
    return waits


async def ask(handler) -> int:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        return await main.cmd_ask(None, http, parse(["ask", "--thread", "t1", "Hi"]))  # ty: ignore[invalid-argument-type]


async def test_a_dropped_answer_goes_on_where_it_was(
    capsys: pytest.CaptureFixture[str], quick: list[float]
) -> None:
    resumed: list[str | None] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            assert request.url.path == "/v1/threads/t1/runs/stream"
            return httpx2.Response(200, content=dropped())
        assert request.url.path == "/v1/threads/t1/runs/r1/stream"
        resumed.append(request.headers.get("Last-Event-ID"))
        if len(resumed) == 1:
            return httpx2.Response(503, text="starting")
        return httpx2.Response(200, content=rest())

    assert await ask(handler) == 0
    out = capsys.readouterr()
    assert out.out.startswith("Hello")
    assert "(Lost the connection to Gen9; reconnecting…)" in out.err
    assert "Continue this chat: gen9 ask --thread t1" in out.err
    assert resumed == ["2", "2"] and quick == [1, 2]


async def test_it_gives_up_saying_where_the_answer_is(
    capsys: pytest.CaptureFixture[str], quick: list[float]
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(200, content=dropped())
        raise httpx2.ConnectError("connection refused")

    assert await ask(handler) == 1
    err = capsys.readouterr().err
    assert "Lost the connection to Gen9. It goes on answering" in err
    assert 'gen9 ask --thread t1 "…"' in err
    assert quick == [1, 2, 4, 8, 16]


async def test_a_drop_before_the_run_is_known_is_a_failure_to_reach_gen9() -> None:
    async def before():
        raise httpx2.RemoteProtocolError("peer closed connection")
        yield b""

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=before())

    with pytest.raises(httpx2.RemoteProtocolError):
        await ask(handler)


async def test_ctrl_c_before_the_run_is_known_still_stops_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ctrl-C before the run's first event (P2-K1): the chat's active run with this message is
    looked up, once the API has recorded it, and stopped."""
    lookups: list[str] = []
    cancelled: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST" and request.url.path.endswith("/runs/stream"):
            raise asyncio.CancelledError  # Ctrl-C while the message is on its way
        if request.method == "GET" and request.url.path == "/v1/threads/t1":
            lookups.append("GET")
            active = (
                {"id": "r9", "status": "running", "message": "Hi"}
                if len(lookups) > 1
                else None
            )
            return httpx2.Response(200, json={"id": "t1", "active_run": active})
        if (
            request.method == "POST"
            and request.url.path == "/v1/threads/t1/runs/r9/cancel"
        ):
            cancelled.append("r9")
            return httpx2.Response(202, json={})
        return httpx2.Response(404)

    assert await ask(handler) == 130
    assert lookups == ["GET", "GET"] and cancelled == ["r9"]
    err = capsys.readouterr().err
    assert "Stopped." in err and 'gen9 ask --thread t1 "…"' in err


async def test_a_message_the_model_didnt_stream_shows_when_it_completes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # P6-Z1: a step budget's stop and an answer cut off at its length limit come only as
    # message.completed; streamed text isn't shown twice
    async def stream():
        yield event("run.queued", '{"run_id": "r1"}', 1)
        yield event("message.delta", '{"id": "m1", "text": "Here: 1, "}', 2)
        yield event("message.delta", '{"id": "m1", "text": "2"}', 3)
        yield event(
            "message.completed",
            '{"id": "m1", "text": "Here: 1, 2\\n\\nI stopped here."}',
            4,
        )
        yield event("message.delta", '{"id": "m2", "text": " Next."}', 5)
        yield event("message.completed", '{"id": "m2", "text": " Next."}', 6)
        yield event(
            "message.completed", '{"id": "", "text": " Stopped at the budget."}', 7
        )
        yield event("run.completed", '{"status": "success", "error": null}', 8)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=stream())

    assert await ask(handler) == 0
    assert capsys.readouterr().out.startswith(
        "Here: 1, 2\n\nI stopped here. Next. Stopped at the budget."
    )
