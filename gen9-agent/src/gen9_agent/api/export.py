"""A person's data as one ZIP, `GET /v1/me/export`: what they gave Gen9, "in a structured, commonly
used and machine-readable format" (GDPR Art. 20; ChatGPT and Claude offer the same, a ZIP of
JSON: docs/plans/manual-e2e.md, P3-E1). Settings downloads it through gen9-ui.

It holds what the API already returns to them, through the same functions, so it says what the
app shows: the account, every chat as `GET /v1/threads/{id}` returns it, memory, notifications and
controls, scheduled tasks, connectors, environment secrets, plugins, and the chats' files; and
what Gen9 and its sign-in service keep about them: usage, audit events, the apps they allowed and
their sign-in records. Never a
token, a secret's value or a trigger's key: those outputs carry none. Each export is in the audit
log.

The ZIP is written to a temporary file (in memory up to 32 MB, then on disk) in a thread, as
zipfile has no async API, then streamed from it.
"""

import asyncio
import json
import logging
import tempfile
import zipfile
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import IO, Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, select, text

from .. import audit
from ..auth import CurrentPrincipal, Principal
from ..deps import Session
from ..keycloak_admin import KeycloakAdminError
from ..memory import read_memory
from ..models import AuditEvent, ChatFile, Thread
from ..settings import Settings
from ..users import CurrentUser
from .connectors import list_connectors
from .environment_secrets import list_secrets
from .me import get_controls, get_notifications, me, my_security
from .my_plugins import list_my_plugins
from .tasks import list_tasks
from .threads import get_thread

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["me"])

SPOOL_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

README = """Your data from Gen9, as of {when}.

account.json               who you are here: your account, your sign-in methods (never a
                           secret), notifications, and what you let Gen9 do with your chats
conversations.json         every chat, its messages as Gen9 shows them: what you asked, what it
                           answered, the steps it took, its sources, the files each turn shared
memory.md                  what Gen9 remembers about you
tasks.json                 your scheduled tasks (a trigger's key is never kept, so it isn't here)
connectors.json            the services you connected, and their tools (no tokens)
environment-secrets.json   your environment secrets' names and hosts, and which requests each goes
                           with (never their values)
plugins.json               the plugins you added
files/<chat>/<name>        the files in your chats: what you attached and what Gen9 made
usage.json                 your use of AI models, day by day: requests, tokens and cost per model
audit.json                 the record of your security and account actions, and of what an
                           administrator or Gen9 did to your account
apps.json                  the apps you let use your account, what each may do and when you
                           allowed it: gen9-cli is Gen9's terminal, gen9-mcp agents that use Gen9
                           over MCP or A2A, and a URL an app that registered itself
sign-ins.json              your sign-in records, as the sign-in service keeps them (30 days): when,
                           what happened, through which app, from which address

Gen9 is an AI system: its answers and the files it made are AI-generated. Each is marked in the
JSON with "ai_generated": true (the EU AI Act, Art. 50(2)); what you wrote and attached is marked
false. A file's "origin" says the same: "output" if Gen9 made it, "upload" if you attached it.

The JSON is UTF-8. Chats are named by their id, as in conversations.json.
"""


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


def _dump(items: Sequence[BaseModel] | BaseModel) -> Any:
    if isinstance(items, BaseModel):
        return items.model_dump(mode="json")
    return [item.model_dump(mode="json") for item in items]


def file_name(folder: str, name: str, taken: set[str]) -> str:
    """Where a chat's file goes in the ZIP: its own name, never a path out of its folder, and
    numbered when the chat has two of the same name."""
    base = PurePosixPath(name.replace("\\", "/")).name
    if base in ("", ".", ".."):
        base = "file"
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem:
        stem, ext = base, ""
    candidate, n = base, 1
    while f"{folder}/{candidate}" in taken:
        n += 1
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
    path = f"{folder}/{candidate}"
    taken.add(path)
    return path


def _write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    archive.writestr(name, data)


# What else Gen9 keeps about the person, for GDPR Art. 15's "personal data concerning him or her"
# (docs/plans/manual-e2e.md, P5-B5). Each part says so if it can't be read now, rather than the
# whole export failing.
UNAVAILABLE = "Couldn't be read just now: export again later."


async def _sign_in_methods(principal: Principal, request: Request) -> Any:
    keycloak = request.app.state.keycloak_admin
    if keycloak is None:
        return {"unavailable": UNAVAILABLE}
    try:
        return _dump(await my_security(principal, keycloak))
    except HTTPException:
        return {"unavailable": UNAVAILABLE}


def _at(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else None


async def _apps(principal: Principal, request: Request) -> Any:
    """The apps the person let use their account (Keycloak's consents), as Settings lists them."""
    keycloak = request.app.state.keycloak_admin
    if keycloak is None:
        return {"unavailable": UNAVAILABLE}
    try:
        consents = await keycloak.consents(principal.sub)
    except KeycloakAdminError:
        return {"unavailable": UNAVAILABLE}
    return [
        {
            "app": c["clientId"],
            "scopes": sorted(c.get("grantedClientScopes") or []),
            "allowed_at": _at(c.get("createdDate")),
            "changed_at": _at(c.get("lastUpdatedDate")),
        }
        for c in consents
    ]


async def _sign_ins(principal: Principal, request: Request) -> Any:
    """The person's sign-in records, as the sign-in service keeps them (30 days): when, what,
    through which app and from which address, and why one failed (docs/plans/gen9-learn.md, M9,
    F14)."""
    keycloak = request.app.state.keycloak_admin
    if keycloak is None:
        return {"unavailable": UNAVAILABLE}
    try:
        events = await keycloak.events(principal.sub)
    except KeycloakAdminError:
        return {"unavailable": UNAVAILABLE}
    return [
        {
            "at": _at(e.get("time")),
            "type": e.get("type"),
            "app": e.get("clientId"),
            "ip": e.get("ipAddress"),
            **({"error": e["error"]} if e.get("error") else {}),
        }
        for e in events
    ]


async def _usage(settings: Settings, sub: str) -> Any:
    """The model router's day-by-day totals for the person (gen9-models' admin API; its key for
    this API may read usage, nothing else)."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.get(
                f"{settings.gen9_models_admin_url}/users/{quote(sub, safe='')}/usage",
                headers={
                    "Authorization": f"Bearer {settings.gen9_models_key.get_secret_value()}"
                },
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        log.warning("export: model usage not read: %s", e)
        return {"unavailable": UNAVAILABLE}


async def _audit(session: Session, sub: str) -> list[dict[str, Any]]:
    """The person's own audit events, and those about their account: who did it named as the
    person, an administrator or Gen9 (another person's id isn't theirs to have)."""
    rows = await session.scalars(
        select(AuditEvent)
        .where(or_(AuditEvent.actor == sub, AuditEvent.target == sub))
        .order_by(AuditEvent.at, AuditEvent.id)
    )
    return [
        {
            "at": row.at.isoformat(),
            "by": "you"
            if row.actor == sub
            else "an administrator"
            if row.action.startswith("admin.")
            else "Gen9",
            "action": row.action,
            "outcome": row.outcome,
            "target": "you" if row.target == sub else row.target,
            "where": row.where,
            "detail": row.detail,
        }
        for row in rows
    ]


async def _stream(spool: IO[bytes]) -> AsyncIterator[bytes]:
    try:
        while chunk := await asyncio.to_thread(spool.read, CHUNK_BYTES):
            yield chunk
    finally:
        spool.close()


@router.get(
    "/me/export",
    summary="Everything Gen9 holds that you gave it, as a ZIP of JSON and your files",
    response_class=StreamingResponse,
)
async def export(
    principal: CurrentPrincipal, user: CurrentUser, session: Session, request: Request
) -> StreamingResponse:
    # One export at a time per person: each is a ZIP of everything, built here before it streams.
    # Postgres holds the lock until this request's transaction ends, across the API's processes
    building = await session.scalar(
        text("select pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": f"export:{user.id}"},
    )
    if not building:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Your export is already being made. Try again once it has downloaded.",
            headers={"Retry-After": "30"},
        )
    now = datetime.now(UTC)
    threads = list(
        await session.scalars(
            select(Thread)
            .where(Thread.user_id == user.id, Thread.deleted_at.is_(None))
            .order_by(Thread.created_at)
        )
    )
    memory = await read_memory(request.app.state.runtime.store, user.sub)
    settings = request.app.state.runtime.settings
    parts: dict[str, bytes] = {
        "README.txt": README.format(when=now.strftime("%Y-%m-%d %H:%M UTC")).encode(),
        "account.json": _json(
            {
                **_dump(await me(principal, user)),
                "sign_in_methods": await _sign_in_methods(principal, request),
                "notifications": _dump(await get_notifications(user, request)),
                "controls": _dump(await get_controls(user)),
            }
        ),
        "conversations.json": _json(
            [_dump(await get_thread(thread, session, request)) for thread in threads]
        ),
        "memory.md": memory.content.encode(),
        "tasks.json": _json(_dump(await list_tasks(user, session, request))),
        "connectors.json": _json(_dump(await list_connectors(user, session, request))),
        "environment-secrets.json": _json(_dump(await list_secrets(user, session))),
        "plugins.json": _json(_dump(await list_my_plugins(user, session, request))),
        "usage.json": _json(await _usage(settings, user.sub)),
        "audit.json": _json(await _audit(session, user.sub)),
        "apps.json": _json(await _apps(principal, request)),
        "sign-ins.json": _json(await _sign_ins(principal, request)),
    }
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115 (closed by _stream)
    archive = zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED)
    try:
        for name, data in parts.items():
            await asyncio.to_thread(_write, archive, name, data)
        # The files one at a time, so a chat's 250 MB never sits in memory at once
        taken: set[str] = set()
        files = (
            await session.execute(
                select(ChatFile.id, ChatFile.thread_id, ChatFile.name)
                .where(ChatFile.thread_id.in_([t.id for t in threads]))
                .order_by(ChatFile.thread_id, ChatFile.created_at)
            )
        ).all()
        for file_id, thread_id, name in files:
            content = await session.scalar(
                select(ChatFile.content).where(ChatFile.id == file_id)
            )
            if content is not None:
                path = file_name(f"files/{thread_id}", name, taken)
                await asyncio.to_thread(_write, archive, path, content)
        await asyncio.to_thread(archive.close)
        await asyncio.to_thread(spool.seek, 0)
    except BaseException:
        spool.close()
        raise
    await audit.record(
        request,
        user.sub,
        "account.export",
        target=user.id,
        detail={"chats": len(threads), "files": len(files)},
    )
    # Built: the lock and its connection go before the download, which can take a while
    await session.commit()
    return StreamingResponse(
        _stream(spool),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="gen9-export-{now:%Y-%m-%d}.zip"',
            "Cache-Control": "no-store",
        },
    )
