"""Every API response carries OWASP's REST headers (headers.py), a route's own kept, and a
streamed answer still streams through the middleware."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from gen9_agent.headers import SecurityHeaders

pytestmark = pytest.mark.asyncio


def app() -> FastAPI:
    api = FastAPI()

    @api.get("/json")
    async def plain() -> dict:
        return {"ok": True}

    @api.get("/cached")
    async def cached() -> JSONResponse:
        return JSONResponse({"ok": True}, headers={"Cache-Control": "max-age=60"})

    @api.get("/stream")
    async def stream() -> StreamingResponse:
        async def events():
            for i in range(3):
                yield f"data: {i}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    api.add_middleware(SecurityHeaders)
    return api


async def test_every_response_is_not_cached_framed_or_sniffed() -> None:
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        plain = await client.get("/json")
        missing = await client.get("/nowhere")
        cached = await client.get("/cached")
        chunks = []
        async with client.stream("GET", "/stream") as streamed:
            async for chunk in streamed.aiter_text():
                chunks.append(chunk)
    for response in (plain, missing, streamed):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert cached.headers["cache-control"] == "max-age=60"  # the route's own, once
    assert "".join(chunks) == "data: 0\n\ndata: 1\n\ndata: 2\n\n"
    # Over plain http, no HSTS (RFC 6797 7.2)
    assert "strict-transport-security" not in plain.headers


async def test_hsts_when_the_api_is_served_over_https() -> None:
    """As the web app sends it, when its public address is https (P4-E1)."""
    api = FastAPI()

    @api.get("/json")
    async def plain() -> dict:
        return {"ok": True}

    api.add_middleware(SecurityHeaders, hsts=True)
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as client:
        response = await client.get("/json")
    assert (
        response.headers["strict-transport-security"]
        == "max-age=63072000; includeSubDomains"
    )
