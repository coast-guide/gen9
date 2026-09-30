"""The connector directory's copy of an MCP registry (directory.py): which servers a connector can
use, and one page of the registry's API."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from gen9_agent.directory import entry_of, page

pytestmark = pytest.mark.asyncio


def item(remotes, status="active", **server) -> dict:
    return {
        "server": {
            "name": "com.example/notes",
            "version": "1.2.0",
            "description": "Notes",
            "remotes": remotes,
            **server,
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": status,
                "updatedAt": "2026-09-20T10:00:00Z",
            }
        },
    }


async def test_a_plain_remote_server_becomes_an_entry():
    found = entry_of(
        item(
            [{"type": "streamable-http", "url": "https://mcp.example.com/mcp"}],
            title="Notes",
            repository={"url": "https://github.com/example/notes"},
        )
    )
    assert found is not None
    assert (found.name, found.url, found.header, found.title) == (
        "com.example/notes",
        "https://mcp.example.com/mcp",
        None,
        "Notes",
    )
    assert found.repository_url == "https://github.com/example/notes"
    assert found.status == "active" and found.updated_at == datetime(
        2026, 9, 20, 10, tzinfo=UTC
    )


async def test_one_secret_header_becomes_the_token_it_asks_for():
    header = {
        "name": "Authorization",
        "value": "Bearer {api_key}",
        "isRequired": True,
        "isSecret": True,
        "description": "Your API key",
    }
    found = entry_of(
        item(
            [
                {
                    "type": "streamable-http",
                    "url": "https://gw.example.com/mcp",
                    "headers": [header],
                }
            ]
        )
    )
    assert found is not None and (found.header, found.header_description) == (
        "Authorization",
        "Your API key",
    )
    raw = entry_of(
        item(
            [
                {
                    "type": "streamable-http",
                    "url": "https://gw.example.com/mcp",
                    "headers": [{**header, "name": "X-Api-Key", "value": "{key}"}],
                }
            ]
        )
    )
    assert raw is not None and raw.header == "X-Api-Key"


@pytest.mark.parametrize(
    "remotes",
    [
        [],  # a local package only
        [{"type": "sse", "url": "https://old.example.com/sse"}],
        [
            {"type": "streamable-http", "url": "https://{tenant}.example.com/mcp"}
        ],  # needs a value filled in
        [
            {
                "type": "streamable-http",
                "url": "https://x.example.com/mcp",
                "headers": [
                    {"name": "A", "value": "{a}", "isRequired": True},
                    {"name": "B", "value": "{b}", "isRequired": True},
                ],
            }
        ],
        [
            {
                "type": "streamable-http",
                "url": "https://x.example.com/mcp",
                "headers": [
                    {
                        "name": "Authorization",
                        "value": "Basic {user}:{password}",
                        "isRequired": True,
                    }
                ],
            }
        ],
    ],
)
async def test_servers_a_connector_cant_use_are_left_out(remotes):
    assert entry_of(item(remotes)) is None


async def test_an_optional_header_is_not_asked_for():
    found = entry_of(
        item(
            [
                {
                    "type": "streamable-http",
                    "url": "https://x.example.com/mcp",
                    "headers": [{"name": "X-Trace", "value": "{id}"}],
                }
            ]
        )
    )
    assert found is not None and found.header is None


async def test_a_page_asks_for_the_latest_versions_since_the_last_sync():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "servers": [item([])],
                "metadata": {"count": 1, "nextCursor": "com.example/notes:1.2.0"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        servers, cursor = await page(
            http,
            "https://registry.test/",
            "com.a/b:1.0.0",
            datetime(2026, 9, 25, 8, tzinfo=UTC),
        )
    query = {k: v[0] for k, v in parse_qs(urlsplit(str(seen[0].url)).query).items()}
    assert str(seen[0].url).startswith("https://registry.test/v0.1/servers?")
    assert query == {
        "limit": "100",
        "version": "latest",
        "cursor": "com.a/b:1.0.0",
        "updated_since": "2026-09-25T08:00:00Z",
    }
    assert len(servers) == 1 and cursor == "com.example/notes:1.2.0"
