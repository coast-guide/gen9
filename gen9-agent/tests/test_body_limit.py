"""Request bodies bounded before anything reads one (body_limit.py; P4-D1)."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from gen9_agent.body_limit import BodyLimit

pytestmark = pytest.mark.asyncio

LIMIT = 1024


class Note(BaseModel):
    text: str


def api() -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    reached: list[str] = []

    @app.post("/notes")
    async def notes(note: Note) -> dict:
        reached.append(note.text[:10])
        return {"length": len(note.text)}

    @app.post("/files")
    async def files(request: Request) -> dict:
        size = sum([len(chunk) async for chunk in request.stream()])
        return {"size": size}

    app.add_middleware(
        BodyLimit, default=LIMIT, larger=[(re.compile(r"^/files$"), 4 * LIMIT)]
    )
    return app, reached


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    )


async def chunks(total: int, size: int = 256) -> AsyncIterator[bytes]:
    body = b'{"text": "' + b"a" * (total - 12) + b'"}'
    for i in range(0, len(body), size):
        yield body[i : i + size]


async def test_a_body_within_the_limit_goes_through() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post("/notes", json={"text": "hello"})
    assert r.status_code == 200 and reached == ["hello"]


async def test_a_declared_length_over_the_limit_is_refused_unread() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post("/notes", json={"text": "a" * 2 * LIMIT})
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]
    assert reached == []


async def test_a_chunked_body_is_refused_once_past_the_limit() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post("/notes", content=chunks(3 * LIMIT))
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert r.status_code == 413 and reached == []
    # Not the 400 FastAPI makes of other errors while it parses a body
    assert "too large" in r.json()["detail"]


async def test_a_path_with_a_larger_limit_has_it() -> None:
    app, _ = api()
    async with client(app) as http:
        within = await http.post("/files", content=chunks(3 * LIMIT))
        over = await http.post("/files", content=chunks(5 * LIMIT))
    assert within.status_code == 200 and within.json()["size"] == 3 * LIMIT
    assert over.status_code == 413


async def test_an_app_without_handlers_is_answered_too() -> None:
    async def read(request: Request) -> PlainTextResponse:
        return PlainTextResponse(str(len(await request.body())))

    app = BodyLimit(
        Starlette(routes=[Route("/", read, methods=["POST"])]), default=LIMIT
    )
    async with client(app) as http:
        ok = await http.post("/", content=b"x" * 10)
        over = await http.post("/", content=chunks(2 * LIMIT))
    assert ok.text == "10"
    assert over.status_code == 413


async def test_a_person_file_limit_is_named_in_gb_or_mb() -> None:
    from gen9_agent.api.files import _size

    assert _size(10 * 1024**3) == "10 GB"
    assert _size(512 * 1024**2) == "512 MB"
    assert _size(1000) == "1 MB"
