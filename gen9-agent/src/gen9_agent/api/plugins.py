"""Plugins, for admins (realm role gen9-admin): the git repositories they come from, and whether
people may have each one (plugin_sources.py; docs/plans/harness.md, milestone 4). The worker does
the fetching: adding a source, or "Sync now", starts its sync and returns."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from temporalio.client import Client
from temporalio.common import (
    Priority,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)

from .. import audit, plugin_connectors
from ..deps import AdminPrincipal, Session
from ..models import Plugin, PluginFile, PluginSource
from ..plugin_skills import fingerprint
from ..plugin_sources import SourceError, remote
from ..temporal import GEN9_KIND
from ..users import upsert_user
from ..workflows.names import (
    PRIORITY_MAINTENANCE,
    SYSTEM_QUEUE,
    plugin_source_workflow_id,
)
from ..workflows.plugins import SyncPluginSourceWorkflow

router = APIRouter(prefix="/v1/admin", tags=["admin: plugins"])
_REF = re.compile(r"^(?!-)[A-Za-z0-9._/-]{1,200}$")


class SourceIn(BaseModel):
    url: str = Field(max_length=2048, description="The repository, https")
    ref: str | None = Field(
        default=None, max_length=200, description="A branch or tag; none: its default"
    )


class SourceOut(BaseModel):
    id: uuid.UUID
    url: str
    ref: str | None
    name: str | None
    description: str | None
    format: str | None
    commit: str | None
    status: str
    error: str | None
    synced_at: datetime | None
    # When a sync last succeeded: a failed source still offers what it synced then
    succeeded_at: datetime | None
    plugins: int


class SkillOut(BaseModel):
    name: str
    description: str


class ServerOut(BaseModel):
    name: str
    type: str
    connects: bool  # Gen9 connects to it (streamable HTTP); others are never run
    url: str | None = None


class SkippedOut(BaseModel):
    what: str
    why: str


class FileOut(BaseModel):
    path: str  # relative to the plugin's root: skills/<skill>/SKILL.md
    size: int


class FileContentOut(BaseModel):
    path: str
    size: int
    # Its text, up to MAX_SHOWN bytes; None when it isn't UTF-8 text
    text: str | None
    cut: bool  # longer than MAX_SHOWN: only its start is here


# What an admin reads of one file here: a skill's instructions fit many times over
MAX_SHOWN = 200_000


class PluginOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    name: str
    title: str | None
    description: str | None
    version: str | None
    format: str | None
    status: str
    reason: str | None
    availability: str
    skills: list[SkillOut]
    mcp_servers: list[ServerOut]
    skipped: list[SkippedOut]
    notes: list[str]
    synced_at: datetime
    # The commit its files came from, to read it at its source
    commit: str | None
    # What it is now (plugin_skills.fingerprint), for the admin's choice to name what they saw
    fingerprint: str | None
    # It changed since an admin chose who may have it: nobody gets it until they look again
    changed: bool
    # The files Gen9 keeps of it (its skills'), for the admin to read what they say
    files: list[FileOut] = []


class SyncStarted(BaseModel):
    source_id: uuid.UUID


class AvailabilityIn(BaseModel):
    availability: Literal["off", "available", "installed"]
    # The plugin as the admin saw it (`fingerprint`): refused if it changed since
    fingerprint: str | None = None


def _plugin_out(
    p: Any, now: str | None, files: list[FileOut] | None = None
) -> PluginOut:
    """`now`: its fingerprint (plugin_skills.fingerprint)."""
    report: dict[str, Any] = p.report or {}
    return PluginOut(
        id=p.id,
        source_id=p.source_id,
        name=p.name,
        title=p.title,
        description=p.description,
        version=p.version,
        format=p.format,
        status=p.status,
        reason=p.reason,
        availability=p.availability,
        skills=[
            SkillOut(name=s["name"], description=s["description"])
            for s in report.get("skills", [])
        ],
        mcp_servers=[
            ServerOut(
                name=s["name"],
                type=s["type"],
                connects=s.get("connects", False),
                url=(s.get("config") or {}).get("url"),
            )
            for s in report.get("mcp_servers", [])
        ],
        skipped=[
            SkippedOut(what=s["what"], why=s["why"]) for s in report.get("skipped", [])
        ]
        + [
            SkippedOut(what=r["field"], why=r["why"])
            for r in report.get("reported", [])
        ],
        notes=report.get("notes", []),
        synced_at=p.synced_at,
        commit=p.commit,
        fingerprint=now,
        changed=p.availability != "off" and p.reviewed != now,
        files=files or [],
    )


async def _start_sync(temporal: Client, source_id: uuid.UUID) -> None:
    """Starts the source's sync; one at a time: asking again while it runs joins it."""
    await temporal.start_workflow(
        SyncPluginSourceWorkflow.run,
        str(source_id),
        id=plugin_source_workflow_id(str(source_id)),
        task_queue=SYSTEM_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        search_attributes=TypedSearchAttributes(
            [SearchAttributePair(GEN9_KIND, "plugins")]
        ),
        priority=Priority(priority_key=PRIORITY_MAINTENANCE),
    )


async def _sources(
    session: Session, source_id: uuid.UUID | None = None
) -> list[SourceOut]:
    count = (
        select(Plugin.source_id, func.count().label("plugins"))
        .group_by(Plugin.source_id)
        .subquery()
    )
    query = (
        select(PluginSource, func.coalesce(count.c.plugins, 0).label("plugins"))
        .outerjoin(count, count.c.source_id == PluginSource.id)
        .order_by(PluginSource.created_at)
    )
    if source_id:
        query = query.where(PluginSource.id == source_id)
    return [
        SourceOut(
            id=s.id,
            url=s.url,
            ref=s.ref,
            name=s.name,
            description=s.description,
            format=s.format,
            commit=s.commit,
            status=s.status,
            error=s.error,
            synced_at=s.synced_at,
            succeeded_at=s.succeeded_at,
            plugins=n,
        )
        for s, n in (await session.execute(query)).all()
    ]


@router.get("/plugin-sources", summary="The repositories plugins come from")
async def list_sources(_: AdminPrincipal, session: Session) -> list[SourceOut]:
    return await _sources(session)


@router.post(
    "/plugin-sources",
    status_code=status.HTTP_201_CREATED,
    summary="Add a repository with a plugin marketplace; its sync starts",
)
async def add_source(
    body: SourceIn, principal: AdminPrincipal, session: Session, request: Request
) -> SourceOut:
    """The URL must be https and resolve to public addresses (the worker checks again, and pins
    the address it fetches from). The marketplace is read by the sync, which reports what it
    found or why it failed on the source."""
    ref = body.ref.strip() if body.ref and body.ref.strip() else None
    if ref is not None and not _REF.match(ref):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't a branch or tag."
        )
    try:
        checked = await remote(body.url, request.app.state.runtime.settings)
    except SourceError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    user = await upsert_user(session, principal.sub, principal.email, principal.name)
    source = PluginSource(url=checked.url, ref=ref, added_by=user.id)
    session.add(source)
    try:
        await session.commit()
    except IntegrityError as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That repository is already a source."
        ) from e
    await _start_sync(request.app.state.temporal, source.id)
    await audit.record(
        request,
        principal.sub,
        "admin.plugin_source.add",
        target=source.id,
        detail={"url": checked.url, "ref": ref},
    )
    return (await _sources(session, source.id))[0]


@router.post(
    "/plugin-sources/{source_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fetch a source again now (a daily Schedule also does)",
)
async def sync_source(
    source_id: uuid.UUID, principal: AdminPrincipal, session: Session, request: Request
) -> SyncStarted:
    if await session.get(PluginSource, source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such source")
    await _start_sync(request.app.state.temporal, source_id)
    await audit.record(
        request, principal.sub, "admin.plugin_source.sync", target=source_id
    )
    return SyncStarted(source_id=source_id)


@router.delete(
    "/plugin-sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a source and every plugin from it",
)
async def remove_source(
    source_id: uuid.UUID, principal: AdminPrincipal, session: Session, request: Request
) -> Response:
    """Its plugins go too, and with them the connectors they brought people (their tokens
    revoked first, where their servers offer that)."""
    leaving = list(
        (
            await session.execute(
                select(Plugin.id).where(Plugin.source_id == source_id)
            )
        ).scalars()
    )
    await session.commit()  # no transaction held while their servers answer
    await plugin_connectors.revoke_for_plugins(request.app.state.runtime, leaving)
    gone = await session.execute(
        delete(PluginSource)
        .where(PluginSource.id == source_id)
        .returning(PluginSource.id)
    )
    if gone.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such source")
    await session.commit()
    await audit.record(
        request,
        principal.sub,
        "admin.plugin_source.remove",
        target=source_id,
        detail={"plugins": len(leaving)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/plugins", summary="Every plugin the sources list, with what each brings")
async def list_plugins(_: AdminPrincipal, session: Session) -> list[PluginOut]:
    rows = await session.execute(
        select(Plugin, fingerprint()).order_by(Plugin.name, Plugin.source_id)
    )
    files: dict[uuid.UUID, list[FileOut]] = {}
    for plugin_id, path, size in await session.execute(
        select(
            PluginFile.plugin_id, PluginFile.path, func.length(PluginFile.content)
        ).order_by(PluginFile.path)
    ):
        files.setdefault(plugin_id, []).append(FileOut(path=path, size=size))
    return [_plugin_out(p, now, files.get(p.id)) for p, now in rows.tuples()]


@router.get(
    "/plugins/{plugin_id}/file",
    summary="What one of a plugin's kept files says, for an admin to read before choosing",
    responses={404: {"description": "No such plugin or file"}},
)
async def read_plugin_file(
    plugin_id: uuid.UUID, path: str, _: AdminPrincipal, session: Session
) -> FileContentOut:
    content = await session.scalar(
        select(PluginFile.content).where(
            PluginFile.plugin_id == plugin_id, PluginFile.path == path
        )
    )
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    return FileContentOut(
        path=path, size=len(content), text=shown(content), cut=len(content) > MAX_SHOWN
    )


def shown(content: bytes, limit: int = MAX_SHOWN) -> str | None:
    """A file's text up to `limit` bytes, or None when it isn't UTF-8 text. A character cut at the
    limit is left out."""
    try:
        return content[:limit].decode("utf-8")
    except UnicodeDecodeError as e:
        return content[: e.start].decode("utf-8") if e.start >= limit - 3 else None


@router.patch("/plugins/{plugin_id}", summary="Choose whether people may have a plugin")
async def set_availability(
    plugin_id: uuid.UUID,
    body: AvailabilityIn,
    principal: AdminPrincipal,
    session: Session,
    request: Request,
) -> PluginOut:
    """off: nobody; available: people install it in Settings; installed: everyone has it. Only
    a plugin that loaded can be made available. Choosing agrees to the plugin as it is now
    (`reviewed`), the one the admin saw (`fingerprint`); after a change, choosing again lets
    people have it again."""
    plugin = await session.get(Plugin, plugin_id)
    if plugin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such plugin")
    if body.availability != "off" and plugin.status != "loaded":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a plugin that loaded can be made available."
        )
    now = await session.scalar(select(fingerprint()).where(Plugin.id == plugin_id))
    if body.fingerprint is not None and body.fingerprint != now:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "It changed since you looked. Look at it again first.",
        )
    await session.execute(
        update(Plugin)
        .where(Plugin.id == plugin_id)
        .values(
            availability=body.availability,
            reviewed=None if body.availability == "off" else now,
        )
    )
    await session.commit()
    await session.refresh(plugin)
    await audit.record(
        request,
        principal.sub,
        "admin.plugin.availability",
        target=plugin_id,
        detail={
            "plugin": plugin.name,
            "availability": body.availability,
            "commit": plugin.commit,
        },
    )
    return _plugin_out(plugin, now)
