"""A plugin's remote MCP servers as a person's connectors (plugin_connectors.py): which servers,
and their names. Making them against a real server is e2e's (`e2e/plugins.mjs`)."""

from types import SimpleNamespace

import pytest

from gen9_agent.connectors import NAME
from gen9_agent.plugin_connectors import connector_name, wanted

pytestmark = pytest.mark.asyncio


async def test_only_remote_servers_without_fixed_headers_become_connectors() -> None:
    plugin = SimpleNamespace(
        report={
            "mcp_servers": [
                {
                    "name": "docs",
                    "connects": True,
                    "config": {
                        "type": "streamable-http",
                        "url": "https://d.example/mcp",
                    },
                },
                {
                    "name": "tenant",
                    "connects": True,
                    "config": {
                        "url": "https://t.example/mcp",
                        "headers": {"X-Tenant": "a"},
                    },
                },
                {
                    "name": "local",
                    "connects": False,
                    "config": {"type": "stdio", "command": "node"},
                },
                {
                    "name": "legacy",
                    "connects": False,
                    "config": {"type": "sse", "url": "https://l.example/sse"},
                },
            ]
        }
    )
    assert wanted(plugin) == {"docs": "https://d.example/mcp"}


async def test_a_servers_connector_name_is_valid_and_new() -> None:
    assert connector_name("docs", set()) == "docs"
    assert connector_name("Docs Server_v2", set()) == "docs-server-v2"
    assert connector_name("docs", {"docs", "docs-2"}) == "docs-3"
    long = connector_name("x" * 40, {"x" * 32})
    assert len(long) <= 32 and long.endswith("-2")
    for name in [
        "docs",
        "docs-server-v2",
        "docs-3",
        long,
        connector_name("!!!", set()),
    ]:
        assert NAME.match(name), name
