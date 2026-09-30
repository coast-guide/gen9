"""The connector directory: Gen9's own copy of an MCP registry (Settings > Connectors, "Browse").

The official MCP Registry "is not intended to be directly consumed by host applications": hosts
read a downstream registry, and aggregators keep their own copy, pulled "on a regular but
infrequent basis (e.g., once per hour)" (modelcontextprotocol.io/registry). So Gen9 keeps its own:
`SyncDirectoryWorkflow` pages through `GET /v0.1/servers` (`updated_since`, `cursor`) of
`MCP_REGISTRY_URL`, any registry with that API, and keeps the servers a connector can use: those with
a streamable-http remote. A deleted server (spam or malware, by the Registry's moderation) leaves the
copy. Entries are not reviewed by Gen9, and Settings says so.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import RegistryServer, RegistrySync
from .workflows.directory import SyncResult

REMOTE_TYPE = "streamable-http"
OFFICIAL_META = "io.modelcontextprotocol.registry/official"
PAGE_SIZE = 100
# A header value Gen9 can fill from one secret: "{key}" or "Bearer {key}"
_ONE_SECRET = re.compile(r"^(Bearer )?\{[A-Za-z0-9_.-]+\}$")


@dataclass(frozen=True)
class Entry:
    """A server as the directory keeps it."""

    name: str  # the registry's name, e.g. com.notion/mcp
    version: str
    title: str | None
    description: str
    url: str  # its streamable-http remote
    # The one header the person must give, sent with each call ("Authorization", …), if any
    header: str | None
    header_description: str | None
    repository_url: str | None
    website_url: str | None
    status: str  # active, deprecated or deleted
    updated_at: datetime | None


def entry_of(item: dict[str, Any]) -> Entry | None:
    """The directory's entry for one item of `GET /v0.1/servers`, or None when a connector can't
    use it: no streamable-http remote, a URL that needs values filled in, or headers Gen9 can't
    fill from one secret."""
    server = item.get("server") or {}
    meta = (item.get("_meta") or {}).get(OFFICIAL_META) or {}
    remote = next(
        (r for r in server.get("remotes") or [] if r.get("type") == REMOTE_TYPE), None
    )
    if not remote or not server.get("name") or "{" in (remote.get("url") or "{"):
        return None
    required = [h for h in remote.get("headers") or [] if h.get("isRequired")]
    if len(required) > 1:
        return None
    header = required[0] if required else None
    if header and not _ONE_SECRET.match(header.get("value") or "{value}"):
        return None
    updated = meta.get("updatedAt") or meta.get("publishedAt")
    return Entry(
        name=server["name"],
        version=str(server.get("version") or ""),
        title=server.get("title"),
        description=(server.get("description") or "")[:1000],
        url=remote["url"],
        header=header["name"] if header else None,
        header_description=(header.get("description") or None) if header else None,
        repository_url=(server.get("repository") or {}).get("url"),
        website_url=server.get("websiteUrl"),
        status=meta.get("status") or "active",
        updated_at=datetime.fromisoformat(updated) if updated else None,
    )


async def page(
    http: httpx.AsyncClient,
    base_url: str,
    cursor: str | None,
    updated_since: datetime | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of `GET /v0.1/servers`, the latest version of each server, and the next cursor.
    With `updated_since`, the registry also returns servers deleted since, so a copy can drop
    them."""
    params: dict[str, str | int] = {"limit": PAGE_SIZE, "version": "latest"}
    if cursor:
        params["cursor"] = cursor
    if updated_since:
        params["updated_since"] = updated_since.isoformat().replace("+00:00", "Z")
    response = await http.get(f"{base_url.rstrip('/')}/v0.1/servers", params=params)
    response.raise_for_status()
    body = response.json()
    return body.get("servers") or [], (body.get("metadata") or {}).get("nextCursor")


# The next sync asks from a little before this one began, so nothing changed during it is missed
SYNC_OVERLAP = timedelta(minutes=5)
# A page is asked again this many times, waiting 2 s, 4 s, 8 s … between, before the Activity fails
PAGE_ATTEMPTS = 4
PAGE_BACKOFF_S = 2


async def _page_retrying(
    http: httpx.AsyncClient,
    registry_url: str,
    cursor: str | None,
    since: datetime | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """A page, asked again after a timeout or a server error: the registry is slow at times."""
    for attempt in range(PAGE_ATTEMPTS):
        try:
            return await page(http, registry_url, cursor, since)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last: Exception = e
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500 and e.response.status_code != 429:
                raise
            last = e
        await asyncio.sleep(2**attempt * PAGE_BACKOFF_S)
    raise last


async def _save_progress(conn, registry_url: str, **values: Any) -> None:
    mark = pg_insert(RegistrySync).values(registry_url=registry_url, **values)
    await conn.execute(
        mark.on_conflict_do_update(
            index_elements=[RegistrySync.registry_url], set_=values
        )
    )


async def sync(
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    registry_url: str,
    *,
    on_page: Callable[[], None] | None = None,
) -> SyncResult:
    """A pass over the registry into `registry_servers`: everything the first time, then what
    changed since the last pass. Each page is saved with the pass's cursor, in one transaction, so
    a pass stopped anywhere (a timeout, a restart, retries run out) resumes where it was, in this
    workflow or the next. `on_page` hears each saved page (the Activity's heartbeat)."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(RegistrySync).where(RegistrySync.registry_url == registry_url)
            )
        ).first()
    since = row.synced_until if row else None
    cursor = row.cursor if row else None
    started = (row.pass_started if row and row.cursor else None) or datetime.now(UTC)
    pages = kept = removed = 0
    while True:
        items, next_cursor = await _page_retrying(http, registry_url, cursor, since)
        upserts: list[dict[str, Any]] = []
        gone: list[str] = []
        for item in items:
            name = (item.get("server") or {}).get("name")
            found = entry_of(item)
            if found is None or found.status == "deleted":
                if name:
                    gone.append(name)
                continue
            upserts.append({**asdict(found), "synced_at": func.now()})
        async with engine.begin() as conn:
            if upserts:
                insert = pg_insert(RegistryServer).values(upserts)
                await conn.execute(
                    insert.on_conflict_do_update(
                        index_elements=[RegistryServer.name],
                        set_={c: insert.excluded[c] for c in upserts[0] if c != "name"},
                    )
                )
            if gone:
                result = await conn.execute(
                    delete(RegistryServer).where(RegistryServer.name.in_(gone))
                )
                removed += result.rowcount or 0
            finished = not next_cursor or not items
            if finished:
                # The next pass asks from a little before this one began
                await _save_progress(
                    conn,
                    registry_url,
                    synced_until=started - SYNC_OVERLAP,
                    cursor=None,
                    pass_started=None,
                )
            else:
                await _save_progress(
                    conn, registry_url, cursor=next_cursor, pass_started=started
                )
        kept += len(upserts)
        pages += 1
        if on_page:
            on_page()
        if finished:
            return SyncResult(pages, kept, removed)
        cursor = next_cursor
