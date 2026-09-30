"""Headers on every response of the API, as OWASP's REST Security Cheat Sheet lists them for
responses a browser may see: not cached, not framed, not sniffed (docs/plans/manual-e2e.md,
P2-B5: `/docs` sent none). Strict-Transport-Security too when the API's public address is https
(RFC 6797: sent over https, never plain http), as the web app does (gen9-ui's proxy.ts), with the
same value: gen9-agent speaks plain http to whatever terminates TLS, but browsers read its
responses there (Temporal UI's codec calls, `/docs`, A2A's sign-in) (manual-e2e.md, P4-E1).

Pure ASGI rather than Starlette's BaseHTTPMiddleware, so streamed answers (SSE) pass through as
they are; a header a route sets itself is kept.
"""

from typing import Any

HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
)


HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class SecurityHeaders:
    def __init__(self, app: Any, hsts: bool = False) -> None:
        self.app = app
        self.headers = (*HEADERS, HSTS) if hsts else HEADERS

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [(n, v) for n, v in self.headers if n not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)
