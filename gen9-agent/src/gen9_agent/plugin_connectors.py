"""A person's plugins' remote MCP servers as their connectors (docs/plans/harness.md, milestone 4):
the rest of what a plugin brings, beside its skills (plugin_skills.py).

- **Which.** Each `streamable-http` server of each plugin the person has (plugins.py marks them
  `connects`), without fixed headers (none of the official marketplaces' servers Gen9 can reach
  has any; supported later if one needs it). `stdio` and `sse` servers are never run.
- **As connectors.** A row in `connectors` naming its plugin and server, made as a person's own
  connector is (connector_setup.py): ready with its tools, or waiting for them to sign in from
  Settings. The same policy ("Ask every time" first) and the same approvals; nothing is connected
  on anyone's behalf until they have the plugin.
- **Kept in line** by `reconcile`: when the person adds or removes a plugin, opens Settings, or
  starts a chat turn. What's missing is made. What they no longer have, or what its plugin no
  longer brings, is removed, with its tokens revoked where its server offers that. Until then a
  run never uses a plugin's connector the person doesn't have (connectors.py).
"""

import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import connector_setup
from .connectors import ConnectorError
from .models import Connector, Plugin, User
from .plugin_skills import has_plugin
from .runtime import Runtime

log = logging.getLogger(__name__)

MAX_NAME = 32
# A server Gen9 couldn't connect to isn't tried again for the same person for this long (a turn
# reconciles too, and shouldn't wait on it every time)
RETRY_S = 600
_failed: dict[tuple[str, uuid.UUID, str], float] = {}


def wanted(plugin: Any) -> dict[str, str]:
    """The servers of a plugin that become connectors: name to URL."""
    return {
        s["name"]: s["config"]["url"]
        for s in (plugin.report or {}).get("mcp_servers", [])
        if s.get("connects") and not (s.get("config") or {}).get("headers")
    }


def connector_name(server: str, taken: set[str]) -> str:
    """A connector name for a plugin's server (lowercase letters, digits and hyphens, at most 32),
    not one of the person's `taken` names."""
    base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", server.lower())).strip("-")
    base = (base or "server")[:MAX_NAME].strip("-")
    name, n = base, 2
    while name in taken:
        suffix = f"-{n}"
        name = base[: MAX_NAME - len(suffix)].rstrip("-") + suffix
        n += 1
    return name


async def drop(runtime: Runtime, rows: list[Connector], sub: str) -> None:
    """Revokes the rows' tokens at their servers, best effort (the rows are deleted by the caller,
    or with their plugin)."""
    for row in rows:
        await runtime.connectors.revoke(row, sub)


async def reconcile(
    runtime: Runtime, session: AsyncSession, user_id: uuid.UUID | None, sub: str
) -> list[str]:
    """Brings the person's plugin connectors in line with the plugins they have: the names of
    those made. A server Gen9 can't reach now is tried again next time."""
    if user_id is None:
        user_id = await session.scalar(select(User.id).where(User.sub == sub))
        if user_id is None:
            return []
    plugins = (
        await session.execute(
            # A plugin whose change waits for an admin keeps its connectors, unused meanwhile
            select(Plugin.id, Plugin.name, Plugin.report).where(
                has_plugin(sub, reviewed=False)
            )
        )
    ).all()
    want = {(p.id, server): url for p in plugins for server, url in wanted(p).items()}
    rows = list(
        await session.scalars(select(Connector).where(Connector.user_id == user_id))
    )
    stale = [
        r
        for r in rows
        if r.plugin_id is not None
        and want.get((r.plugin_id, r.plugin_server or "")) != r.url
    ]
    if stale:
        await session.commit()  # no transaction held while their servers answer
        await drop(runtime, stale, sub)
        await session.execute(
            delete(Connector).where(Connector.id.in_([r.id for r in stale]))
        )
        await session.commit()
    have = {
        (r.plugin_id, r.plugin_server) for r in rows if r.plugin_id and r not in stale
    }
    taken = {r.name for r in rows if r not in stale}
    made = []
    for (plugin_id, server), url in want.items():
        if (plugin_id, server) in have:
            continue
        key = (sub, plugin_id, server)
        if time.monotonic() - _failed.get(key, -RETRY_S) < RETRY_S:
            continue
        name = connector_name(server, taken)
        try:
            row = await connector_setup.make(runtime, user_id, sub, name, url)
        except (ConnectorError, connector_setup.CantKeep) as e:
            log.warning("plugin %s's %s for %s left out: %s", plugin_id, server, sub, e)
            _failed[key] = time.monotonic()
            continue
        _failed.pop(key, None)
        row.plugin_id, row.plugin_server = plugin_id, server
        values = {
            c.key: getattr(row, c.key)
            for c in Connector.__table__.columns
            if getattr(row, c.key) is not None
        }
        # Two requests may reconcile at once (Settings asks for connectors and plugins together):
        # the second insert does nothing, and no rollback expires the caller's objects
        made_now = await session.execute(
            insert(Connector).values(**values).on_conflict_do_nothing()
        )
        await session.commit()
        if not made_now.rowcount:  # ty: ignore[unresolved-attribute]
            continue
        taken.add(name)
        made.append(name)
    return made


async def revoke_for_plugins(runtime: Runtime, plugin_ids: list[uuid.UUID]) -> None:
    """Before plugins go (taken out of their marketplace, or their source removed): their
    connectors' tokens revoked where the servers offer that; the rows go with the plugins."""
    if not plugin_ids:
        return
    async with runtime.engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    Connector.id,
                    Connector.name,
                    Connector.sealed_tokens,
                    Connector.sign_in,
                    Connector.sealed_client_secret,
                    User.sub,
                )
                .join(User, User.id == Connector.user_id)
                .where(Connector.plugin_id.in_(plugin_ids))
            )
        ).all()
    for row in rows:
        await runtime.connectors.revoke(row, row.sub)
