"""The connector directory's Activity (workflows/directory.py): one pass over the MCP registry."""

import httpx
from temporalio import activity

from .directory import sync
from .runtime import Runtime
from .workflows.directory import SyncResult
from .workflows.names import SYNC_DIRECTORY

# The registry answered a page in more than 30 s at times (docs/plans/harness.md)
PAGE_TIMEOUT_S = 90


class DirectoryActivities:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    @activity.defn(name=SYNC_DIRECTORY)
    async def sync_directory(self) -> SyncResult:
        """A pass over `MCP_REGISTRY_URL`, resumed from where Postgres says the last one stopped;
        a heartbeat per page saved."""
        async with httpx.AsyncClient(timeout=PAGE_TIMEOUT_S) as http:
            return await sync(
                self.runtime.engine,
                http,
                self.runtime.settings.mcp_registry_url,
                on_page=activity.heartbeat,
            )

    def all(self) -> list:
        return [self.sync_directory]
