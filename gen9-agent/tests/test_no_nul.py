"""Text with the NUL character refused where it comes in (no_nul.py; P6-B1)."""

import re

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.requests import Request

from gen9_agent.body_limit import BodyLimit
from gen9_agent.no_nul import REFUSAL, NoNul

pytestmark = pytest.mark.asyncio

# Built at run time: an escape typed into this file could arrive as the raw character
BACKSLASH = chr(92)
ESCAPED = BACKSLASH + "u0000"


class Note(BaseModel):
    text: str


def api() -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    reached: list[str] = []

    @app.post("/notes")
    async def notes(note: Note) -> dict:
        reached.append(note.text)
        return {"text": note.text}

    @app.get("/search")
    async def search(q: str) -> dict:
        reached.append(q)
        return {"q": q}

    @app.post("/files")
    async def files(request: Request) -> dict:
        body = b"".join([chunk async for chunk in request.stream()])
        reached.append("file")
        return {"size": len(body), "nuls": body.count(b"\x00")}

    # As app.py has them: NoNul inside BodyLimit, which bounds a body before it's read
    app.add_middleware(NoNul)
    app.add_middleware(
        BodyLimit, default=1024, larger=[(re.compile(r"^/files$"), 4096)]
    )
    return app, reached


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    )


def json_body(text: str) -> bytes:
    return ('{"text": "' + text + '"}').encode()


async def test_a_query_with_a_nul_is_refused_before_the_endpoint() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.get("/search?q=a%00b")
    assert r.status_code == 422 and r.json() == {"detail": REFUSAL}
    assert reached == []


async def test_a_path_with_a_nul_is_refused() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.get("/search%00x?q=a")
    assert r.status_code == 422 and reached == []


async def test_a_json_body_with_an_escaped_nul_is_refused() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post(
            "/notes",
            content=json_body("a" + ESCAPED + "b"),
            headers={"content-type": "application/json; charset=utf-8"},
        )
    assert r.status_code == 422 and r.json() == {"detail": REFUSAL}
    assert reached == []


async def test_an_escaped_backslash_before_u0000_is_text_and_goes_through() -> None:
    app, reached = api()
    text = BACKSLASH + BACKSLASH + "u0000"  # the JSON for a backslash, then "u0000"
    async with client(app) as http:
        r = await http.post(
            "/notes",
            content=json_body(text),
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 200
    assert r.json() == {"text": BACKSLASH + "u0000"}
    assert reached == [BACKSLASH + "u0000"]


async def test_a_plain_json_body_reaches_the_endpoint_whole() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post("/notes", json={"text": "résumé, 履歴書"})
    assert r.status_code == 200 and r.json() == {"text": "résumé, 履歴書"}
    assert reached == ["résumé, 履歴書"]


async def test_a_file_may_hold_nul_bytes() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post(
            "/files",
            content=b"\x00\x01binary\x00",
            headers={"content-type": "application/octet-stream"},
        )
    assert r.status_code == 200 and r.json() == {"size": 9, "nuls": 2}
    assert reached == ["file"]


async def test_a_json_body_over_the_limit_is_still_refused_by_size_first() -> None:
    app, reached = api()
    async with client(app) as http:
        r = await http.post(
            "/notes",
            content=json_body("a" * 2000),
            headers={
                "content-type": "application/json",
                "transfer-encoding": "chunked",
            },
        )
    assert r.status_code == 413 and reached == []
