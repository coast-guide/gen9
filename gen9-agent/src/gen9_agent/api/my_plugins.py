"""A person's plugins (plugin_skills.py): the ones admins made available to them, which they add or
remove, and every skill their chats can use."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from .. import plugin_connectors
from ..deps import Session
from ..models import Plugin, PluginInstall, PluginSource
from ..plugin_skills import fingerprint, has_plugin, skills_chosen
from ..users import CurrentUser

router = APIRouter(prefix="/v1/me", tags=["me: plugins"])


class MySkillOut(BaseModel):
    name: str
    description: str


class MyPluginOut(BaseModel):
    id: uuid.UUID
    name: str
    title: str | None
    description: str | None
    version: str | None
    source: str  # the marketplace it came from
    skills: list[MySkillOut]
    connectors: int  # remote MCP servers it brings
    added: bool  # the person has it
    for_everyone: bool  # an admin gave it to everyone: it can't be removed
    # It changed since an admin chose who may have it: nobody gets it until they look again
    waiting: bool


class SkillOut(BaseModel):
    name: str
    description: str
    plugin: str | None  # None: Gen9's own


def _out(p: Any, added: bool) -> MyPluginOut:
    report: dict[str, Any] = p.report or {}
    return MyPluginOut(
        id=p.id,
        name=p.name,
        title=p.title,
        description=p.description,
        version=p.version,
        source=p.source_name or p.source_url,
        skills=[
            MySkillOut(name=s["name"], description=s["description"])
            for s in report.get("skills", [])
        ],
        connectors=sum(bool(s.get("connects")) for s in report.get("mcp_servers", [])),
        added=added,
        for_everyone=p.availability == "installed",
        waiting=bool(p.waiting),
    )


_COLUMNS = (
    Plugin.id,
    Plugin.name,
    Plugin.title,
    Plugin.description,
    Plugin.version,
    Plugin.report,
    Plugin.availability,
    Plugin.reviewed.is_distinct_from(fingerprint()).label("waiting"),
    PluginSource.name.label("source_name"),
    PluginSource.url.label("source_url"),
)


@router.get("/plugins", summary="The plugins you may add, and the ones you have")
async def list_my_plugins(
    user: CurrentUser, session: Session, request: Request
) -> list[MyPluginOut]:
    # The connectors of plugins given to everyone, made when the person first looks
    await plugin_connectors.reconcile(
        request.app.state.runtime, session, user.id, user.sub
    )
    mine = set(
        (
            await session.execute(
                select(PluginInstall.plugin_id).where(PluginInstall.user_id == user.id)
            )
        ).scalars()
    )
    rows = await session.execute(
        select(*_COLUMNS)
        .join(PluginSource, PluginSource.id == Plugin.source_id)
        .where(
            Plugin.status == "loaded",
            Plugin.availability.in_(("available", "installed")),
        )
        .order_by(Plugin.name)
    )
    return [
        _out(p, added=p.availability == "installed" or p.id in mine) for p in rows.all()
    ]


async def _available(session: Session, plugin_id: uuid.UUID) -> Any:
    row = (
        await session.execute(
            select(*_COLUMNS)
            .join(PluginSource, PluginSource.id == Plugin.source_id)
            .where(
                Plugin.id == plugin_id,
                Plugin.status == "loaded",
                Plugin.availability.in_(("available", "installed")),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such plugin")
    return row


@router.put("/plugins/{plugin_id}", summary="Add a plugin: its skills join your chats")
async def add_my_plugin(
    plugin_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> MyPluginOut:
    """Its remote servers become the person's connectors (waiting for sign-in where their
    servers ask for one)."""
    plugin = await _available(session, plugin_id)
    if plugin.availability == "available":
        await session.execute(
            insert(PluginInstall)
            .values(user_id=user.id, plugin_id=plugin_id)
            .on_conflict_do_nothing()
        )
        await session.commit()
    await plugin_connectors.reconcile(
        request.app.state.runtime, session, user.id, user.sub
    )
    return _out(plugin, added=True)


@router.delete(
    "/plugins/{plugin_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a plugin you added",
)
async def remove_my_plugin(
    plugin_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Response:
    """Its connectors go too, their tokens revoked where their servers offer that."""
    plugin = await _available(session, plugin_id)
    if plugin.availability == "installed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An admin gave everyone this plugin, so it can't be removed.",
        )
    await session.execute(
        delete(PluginInstall).where(
            PluginInstall.user_id == user.id, PluginInstall.plugin_id == plugin_id
        )
    )
    await session.commit()
    await plugin_connectors.reconcile(
        request.app.state.runtime, session, user.id, user.sub
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/skills", summary="Every skill your chats can use: Gen9's own and your plugins'"
)
async def list_my_skills(
    user: CurrentUser, session: Session, request: Request
) -> list[SkillOut]:
    definition = request.app.state.runtime.definition
    plugins = (
        await session.execute(
            select(Plugin.id, Plugin.name, Plugin.title, Plugin.report).where(
                has_plugin(user.sub)
            )
        )
    ).all()
    own = [SkillOut(name=n, description=d, plugin=None) for n, d in definition.skills]
    theirs = [
        SkillOut(
            name=skill["name"],
            description=skill["description"],
            plugin=plugin.title or plugin.name,
        )
        for plugin, skill in skills_chosen(plugins, definition.skill_names)
    ]
    return own + theirs
