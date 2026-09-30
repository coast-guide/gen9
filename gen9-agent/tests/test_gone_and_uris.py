"""500s Schemathesis found (P6-B1): a resource address MCP's client can't parse, a row deleted
while its request was under way, and a connected server's failure."""

import httpx
import pytest
from fastapi import FastAPI
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError
from sqlalchemy.orm.exc import StaleDataError

from gen9_agent.api.connectors import AppResourceIn
from gen9_agent.connectors import failed
from gen9_agent.stale import GONE, gone_meanwhile

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("uri", ["\U00109f60{", "not a uri", "://nothing"])
async def test_a_resource_address_the_client_cant_parse_is_refused(uri: str) -> None:
    with pytest.raises(ValidationError, match="resource's address"):
        AppResourceIn(uri=uri)


@pytest.mark.parametrize(
    "uri", ["ui://widget/board.html", "ui://w", "file:///notes/a.md", "https://x.dev/r"]
)
async def test_a_resource_address_is_kept_exactly_as_written(uri: str) -> None:
    assert AppResourceIn(uri=uri).uri == uri


async def test_a_row_deleted_meanwhile_is_a_404_not_a_500() -> None:
    app = FastAPI()
    app.add_exception_handler(StaleDataError, gone_meanwhile)

    @app.post("/things/{thing}/keep")
    async def keep(thing: str) -> dict:
        raise StaleDataError(
            "UPDATE statement on table 'connectors' expected to update 1 row(s); 0 were matched."
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as http:
        r = await http.post("/things/abc/keep")
    assert r.status_code == 404 and r.json() == {"detail": GONE}


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (MCPError(-32601, "Method not found"), "The server refused: Method not found"),
        (TimeoutError(), "The server didn't answer in time."),
        (
            httpx.ConnectError("All connection attempts failed"),
            "Gen9 couldn't reach that server.",
        ),
        (RuntimeError("HTTP 401 Unauthorized"), "The server refused the token."),
        (ValueError("an answer it couldn't read"), "The server failed to answer."),
    ],
)
async def test_a_servers_failure_is_said_in_words(error: Exception, said: str) -> None:
    assert str(failed(error)) == said
