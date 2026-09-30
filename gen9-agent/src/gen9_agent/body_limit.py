"""A limit on request bodies, enforced before anything holds one (docs/plans/manual-e2e.md, P4-D1).

FastAPI reads and parses a JSON body before it checks who is asking or what the fields say, and
Uvicorn limits no body (its settings bound only incomplete header data). Without this, a caller who
never signed in made the API hold a body of any size: 100 MB was read whole, in 1.75 s, before its
401, the API's memory up by at least 120 MiB (OWASP API4:2023, unrestricted resource consumption).

A declared length over the limit is refused at once, unread. A body sent without one (chunked) is
counted as it arrives and refused the moment it passes the limit: an HTTPException, which FastAPI
lets through its body parsing (it turns any other error there into a 400) and Starlette answers.
413 with a JSON `detail`, as the API's other refusals.
"""

import re
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from typing import Any

from starlette.exceptions import HTTPException

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _too_large(limit: int) -> str:
    return f"The request is too large: at most {limit // 1024} KiB here."


class BodyLimit:
    """At most `default` bytes of body, or the limit of the first `larger` pattern its path matches."""

    def __init__(
        self,
        app: ASGIApp,
        default: int,
        larger: Sequence[tuple[re.Pattern[str], int]] = (),
    ) -> None:
        self.app = app
        self.default = default
        self.larger = larger

    def limit(self, path: str) -> int:
        return next(
            (n for pattern, n in self.larger if pattern.match(path)), self.default
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit(scope["path"])
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _refuse(send, limit)
            return
        seen = 0
        started = False

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(413, _too_large(limit))
            return message

        async def tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except HTTPException as e:
            # An app with no handler of its own let it through: answer here, if nothing was sent
            if e.status_code != 413 or started:
                raise
            await _refuse(send, limit)


async def _refuse(send: Send, limit: int) -> None:
    body = ('{"detail": "' + _too_large(limit) + '"}').encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
