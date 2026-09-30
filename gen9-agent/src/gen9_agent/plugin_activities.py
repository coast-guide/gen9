"""The Activities of plugin sources' sync (workflows/plugins.py)."""

import uuid

from sqlalchemy import select
from temporalio import activity

from .models import PluginSource
from .plugin_sources import sync
from .runtime import Runtime
from .workflows.names import LIST_PLUGIN_SOURCES, SYNC_PLUGIN_SOURCE
from .workflows.plugins import PluginSyncResult


class PluginActivities:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    @activity.defn(name=SYNC_PLUGIN_SOURCE)
    async def sync_plugin_source(self, source_id: str) -> PluginSyncResult:
        """Fetches the source's marketplace and its plugins, and keeps what they bring."""
        s = await sync(self.runtime, uuid.UUID(source_id))
        return PluginSyncResult(s.plugins, s.loaded, s.changed, s.removed, s.problems)

    @activity.defn(name=LIST_PLUGIN_SOURCES)
    async def list_plugin_sources(self) -> list[str]:
        async with self.runtime.engine.connect() as conn:
            rows = await conn.execute(
                select(PluginSource.id).order_by(PluginSource.created_at)
            )
            return [str(r.id) for r in rows]

    def all(self) -> list:
        return [self.sync_plugin_source, self.list_plugin_sources]
