"""The database not taking a request: a 503 in words, not a 500 (docs/plans/manual-e2e.md, P6-B3).

With gen9-postgres refusing writes, as a full disk makes it, every request answered 500, reads
included: each first records who is asking (users.py). Probed live by making the services' role
read-only mid-turn. ASVS 5.0 16.5.2 asks that a failing resource degrade gracefully, and OWASP
A10:2025 that exceptional conditions be handled meaningfully:

- a write refused (`ReadOnlySqlTransaction`, 25006; class 53, insufficient resources: a full
  disk, out of memory): "Gen9 can't save changes right now. Try again later.";
- the database gone mid-request (psycopg's other OperationalErrors: a connection ended, the
  server down, too many connections): "Gen9's database didn't answer. Try again in a moment.".

Both 503 with Retry-After (RFC 9110 15.6.4, 10.2.3). Anything else a route didn't catch goes on to
the last resort, a 500, as before. Reading who is asking falls back to their row as it is
(users.py), so people can still read their chats while nothing can be saved.
"""

import logging

import psycopg
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

CANT_SAVE = "Gen9 can't save changes right now. Try again later."
NO_ANSWER = "Gen9's database didn't answer. Try again in a moment."
RETRY_AFTER_S = 30


# By SQLSTATE, as PostgreSQL's Appendix A lists them: psycopg's classes for 53xxx are siblings
# (its DiskFull isn't an InsufficientResources), so the code is what says it
READ_ONLY = "25006"
INSUFFICIENT_RESOURCES = "53"
TOO_MANY_CONNECTIONS = "53300"


def cannot_write(exc: BaseException) -> bool:
    """The database refused a write: read-only (25006), or out of disk or memory (class 53, but
    too many connections, which is its not answering)."""
    state = getattr(getattr(exc, "orig", exc), "sqlstate", None) or ""
    return state == READ_ONLY or (
        state.startswith(INSUFFICIENT_RESOURCES) and state != TOO_MANY_CONNECTIONS
    )


def unavailable(exc: BaseException) -> str | None:
    """What to tell the person when the database can't take the request, or None: not that."""
    if cannot_write(exc):
        return CANT_SAVE
    if isinstance(getattr(exc, "orig", exc), psycopg.OperationalError):
        return NO_ANSWER
    return None


async def database_unavailable(request: Request, exc: Exception) -> Response:
    """Registered for SQLAlchemy's DBAPIError and psycopg's own Error, which LangGraph's store
    raises unwrapped (app.py); the rest goes on to the last resort."""
    said = unavailable(exc)
    if said is None:
        raise exc
    # The route's template, never the path as sent (stale.py)
    route = getattr(request.scope.get("route"), "path", "?")
    log.warning(
        "%s %s: the database can't take it: %s",
        request.method,
        route,
        type(getattr(exc, "orig", exc)).__name__,
    )
    return JSONResponse(
        {"detail": said},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": str(RETRY_AFTER_S)},
    )
