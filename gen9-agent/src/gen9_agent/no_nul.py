"""Text with the NUL character refused where it comes in (docs/plans/manual-e2e.md, P6-B1).

PostgreSQL's text can't hold U+0000, and its jsonb refuses `\\u0000` ("because that cannot be
represented in PostgreSQL's text type", its JSON types docs), so psycopg raises DataError before a
query with one is sent. A NUL in a search, a memory or a directory query reached that point and
answered 500 (Schemathesis 4.28 over the API, then by hand; OWASP A10:2025, CWE-248). Django's
forms reject it at every text field for the same reason (ProhibitNullCharactersValidator, in each
CharField). Here it's refused once, before routing, for every endpoint, the mounted MCP and A2A
ones included:

- a path or query string holding one (`%00`);
- a JSON body with an escaped one (`\\u0000`, not an escaped backslash before `u0000`), or a raw
  0x00. Other bodies (a file's bytes) aren't read: binary content may hold NUL.

422 with a JSON `detail`, as the API's other refusals. The body is read here only for JSON, which
FastAPI reads whole anyway, and inside BodyLimit (body_limit.py), which bounds it first.
"""

import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

REFUSAL = "Text here can't contain the NUL character (U+0000)."
# \u0000 whose backslash isn't itself escaped: an even run of backslashes (none included) before it
ESCAPED_NUL = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000")


def _json(scope: Scope) -> bool:
    kind = dict(scope.get("headers") or []).get(b"content-type", b"")
    kind = kind.split(b";")[0].strip().lower()
    return kind == b"application/json" or kind.endswith(b"+json")


class NoNul:
    """Refuses a request whose path, query string or JSON body holds U+0000."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        query = scope.get("query_string") or b""
        if "\x00" in scope["path"] or b"%00" in query or b"\x00" in query:
            await _refuse(send)
            return
        if not _json(scope):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        early: list[Message] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client left before the body ended: the app hears it as it would have
                early.append(message)
                break
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        if b"\x00" in body or ESCAPED_NUL.search(body):
            await _refuse(send)
            return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            if early:
                return early.pop(0)
            return await receive()

        await self.app(scope, replay, send)


async def _refuse(send: Send) -> None:
    body = ('{"detail": "' + REFUSAL + '"}').encode()
    await send(
        {
            "type": "http.response.start",
            "status": 422,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
