"""An MCP server that needs sign-in, for e2e/connectors-oauth.mjs: FastMCP with its in-memory OAuth
provider, which is its own authorization server (Protected Resource Metadata, RFC 8414 metadata,
Dynamic Client Registration, PKCE, refresh tokens) and approves every sign-in, as a test server
should. Access tokens last 30 s, so gen9-agent refreshes them on every listing.

Run on the host: uv run --with fastmcp==4.0.9 python e2e/fixtures/oauth_mcp.py <port>
It serves http://host.docker.internal:<port>/mcp, the one address both gen9-agent's containers
(Docker Desktop) and the check's Chrome (mapped to 127.0.0.1) reach. POST /test/revoke forgets every
token, so the next refresh fails. It offers RFC 7009 revocation, and GET /test/revoked says what
clients revoked there and how many tokens are left.

The MCP SDK's server allows an http issuer only on localhost ("RFC 8414 requires HTTPS, but we allow
loopback/localhost HTTP for testing"). A container can't reach the host as localhost, so this test
server relaxes that check for its one http name. Gen9 itself still refuses http outside the hosts its
operator names (CONNECTORS_ALLOWED_HOSTS).

The SDK's /revoke also requires a `client_secret`, so a public client (Gen9's, registered by DCR)
can't revoke there: python-sdk#3508, still open. This test server takes RFC 7009's form, a
public client's `client_id` alone, as the issue's fix does.
"""

import sys

import mcp.server.auth.routes as auth_routes
from mcp.server.auth.handlers import revoke as revoke_handler
from fastmcp import FastMCP
from fastmcp.server.auth.auth import ClientRegistrationOptions, RevocationOptions
from fastmcp.server.auth.providers import in_memory
from starlette.requests import Request
from starlette.responses import JSONResponse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 17801
BASE = f"http://host.docker.internal:{PORT}"
in_memory.DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS = 30
auth_routes.validate_issuer_url = lambda url: None


class RevocationRequest(revoke_handler.RevocationRequest):
    client_secret: str | None = None


revoke_handler.RevocationRequest = RevocationRequest

auth = in_memory.InMemoryOAuthProvider(
    base_url=BASE,
    client_registration_options=ClientRegistrationOptions(enabled=True),
    revocation_options=RevocationOptions(enabled=True),
)
# What clients revoked at /revoke (a known token; the provider revokes its counterpart too)
REVOKED: list[str] = []
_revoke_token = auth.revoke_token


async def revoke_token(token) -> None:
    REVOKED.append(type(token).__name__)
    await _revoke_token(token)


auth.revoke_token = revoke_token
mcp = FastMCP(name="signed-in-notes", auth=auth)


@mcp.tool(annotations={"readOnlyHint": True})
def secret_note() -> str:
    """The note only a signed-in client may read."""
    return "The signed-in note says: lighthouse keepers log the weather at dawn."


@mcp.custom_route("/test/revoke", methods=["POST"])
async def revoke(request: Request) -> JSONResponse:
    auth.access_tokens.clear()
    auth.refresh_tokens.clear()
    return JSONResponse({"revoked": True})


@mcp.custom_route("/test/revoked", methods=["GET"])
async def revoked(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "revoked": REVOKED,
            "access_tokens_left": len(auth.access_tokens),
            "refresh_tokens_left": len(auth.refresh_tokens),
        }
    )


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=PORT, path="/mcp")
