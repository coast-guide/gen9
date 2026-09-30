"""What a request changes, deleted while it was under way: 404, not a 500
(docs/plans/manual-e2e.md, P6-B1).

Some routes read a row, let its transaction go while another service answers (a connector's
server listing its tools), then write the row. Deleted meanwhile, as when the person removes that
connector in another tab, SQLAlchemy's flush matches no row and raises StaleDataError: answered 500
until Schemathesis' stateful phase, two workers at once, did exactly that. OWASP A10:2025 asks for a
handler that catches what a route didn't, so every route gets this one: the thing is gone, as 404
says, and nothing was written (the transaction rolls back).
"""

import logging

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

GONE = "That was removed meanwhile."


async def gone_meanwhile(request: Request, exc: Exception) -> Response:
    """Registered for StaleDataError alone (app.py)."""
    # The route's template, never the path as sent: a decoded %0A would start a log line of its own
    route = getattr(request.scope.get("route"), "path", "?")
    log.info("%s %s: what it changes was deleted meanwhile", request.method, route)
    return JSONResponse({"detail": GONE}, status_code=status.HTTP_404_NOT_FOUND)
