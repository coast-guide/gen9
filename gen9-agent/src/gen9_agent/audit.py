"""Who did what (docs/plans/harness.md, "Auth across the harness"; OWASP ASVS 5.0 V16 and the
Logging Cheat Sheet).

Keycloak logs sign-ins, and its admin events log what Gen9 asks of it. But it sees Gen9's
service account, not the admin who clicked, so only Gen9 can say who acted. Recorded in
`audit_events`, with when, where (the route), who (the person's `sub`), what and the outcome:
- admin actions: users (roles, enabled, sessions, sign-in lock, password reset, deletion),
  plugin sources and plugins;
- people's security actions: deleting their account, adding and removing a connector, an
  environment secret or a task's trigger;
- an answer sent again to a run's question (a replayed approval), or to a run no longer asking;
- access refused: every 403, and one person's ids tried by another (answered 404, so the ids'
  existence isn't revealed to them).

Never recorded: tokens, passwords, secret values, message text. The table is append-only (a
trigger refuses changes), and admins read it at `GET /v1/admin/audit`.

Each record is also one line of the API's log, `audit {…}` in JSON, written before the database
is: what a collector sends to a separate system, where whoever breaks in here can't erase it
(docs/logging.md, "Sending the logs elsewhere"; ASVS 5.0 16.4.3).
"""

import json
import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .log_safety import CONTROL
from .models import AuditEvent

log = logging.getLogger(__name__)

SUCCESS = "success"
DENIED = "denied"


def where(request: Request) -> str:
    """The route as its template (`PATCH /v1/admin/users/{user_id}`), not the URL: ids go in
    `target`, and nothing a caller typed reaches the log unshaped."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return f"{request.method} {path}"[:255]


async def record(
    request: Request,
    actor: str,
    action: str,
    *,
    target: str | uuid.UUID | None = None,
    outcome: str = SUCCESS,
    detail: dict[str, Any] | None = None,
) -> None:
    """Adds an event in its own transaction, after its line in the log. A failure to record is
    logged, not raised: the action it records has already happened."""
    event = AuditEvent(
        actor=actor[:255],
        action=action,
        outcome=outcome,
        target=str(target)[:255] if target is not None else None,
        where=where(request),
        detail=detail or {},
    )
    log.info("audit %s", line(event))
    try:
        async with request.app.state.sessionmaker() as session:
            session.add(event)
            await session.commit()
    except Exception:
        # The action happened; the log says its record didn't
        log.exception("audit: couldn't record %s by %s", action, actor)


def line(event: AuditEvent) -> str:
    """The event as one line of JSON. JSON escapes C0 controls itself; the others log_safety.py
    escapes in its own way (`\\x7f`), which a JSON reader refuses, so they are escaped as JSON's
    `\\u007f` here and the line stays both one line and valid."""
    text = json.dumps(
        {
            "actor": event.actor,
            "action": event.action,
            "outcome": event.outcome,
            "target": event.target,
            "where": event.where,
            "detail": event.detail,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return CONTROL.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


async def theirs(
    request: Request,
    session: AsyncSession,
    model: Any,
    id_: uuid.UUID,
    user: Any,
    kind: str,
) -> None:
    """When `id_` names another person's row of `model` (it has `user_id`), records the attempt
    as refused. A missing id isn't recorded: only a real other person's thing is."""
    owner = await session.scalar(select(model.user_id).where(model.id == id_))
    if owner is not None and owner != user.id:
        await record(request, user.sub, f"{kind}.access", target=id_, outcome=DENIED)


async def theirs_through(
    request: Request,
    session: AsyncSession,
    owner: Select[Any],
    id_: uuid.UUID,
    user: Any,
    kind: str,
) -> None:
    """The same for what belongs to a person through their chat (a run, a file): `owner` selects
    the owner of `id_`, so another person's run or file tried under one's own chat is recorded
    too (found by hand: docs/plans/manual-e2e.md, P2-A1)."""
    found = await session.scalar(owner)
    if found is not None and found != user.id:
        await record(request, user.sub, f"{kind}.access", target=id_, outcome=DENIED)
