"""An MCP server that trusts a Keycloak realm, for e2e/connectors-keycloak.mjs: FastMCP 4.0.9's
KeycloakAuthProvider (Protected Resource Metadata naming the realm; Keycloak's JWTs verified by
issuer, keys, scope and audience).

Run on the host: uv run --with fastmcp==4.0.9 python e2e/fixtures/keycloak_mcp.py <port> <realm> <audience>
It serves http://host.docker.internal:<port>/mcp. The realm's issuer is
http://host.docker.internal:15000/realms/<realm> (its frontend URL, one address for gen9-agent's
containers and the check's Chrome); this process runs on the host, which can't resolve that name,
so it reads the realm's keys at localhost:15000 and checks the issuer as the realm states it.
"""

import sys

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 17804
REALM = sys.argv[2]
AUDIENCE = sys.argv[3]
ISSUER = f"http://host.docker.internal:15000/realms/{REALM}"

verifier = JWTVerifier(
    jwks_uri=f"http://localhost:15000/realms/{REALM}/protocol/openid-connect/certs",
    issuer=ISSUER,
    algorithm="RS256",
    required_scopes=["openid"],
    audience=AUDIENCE,
)
auth = KeycloakAuthProvider(
    realm_url=ISSUER,
    base_url=f"http://host.docker.internal:{PORT}",
    required_scopes=["openid"],
    audience=AUDIENCE,
    token_verifier=verifier,
)
mcp = FastMCP(name="team-notes", auth=auth)


@mcp.tool(annotations={"readOnlyHint": True})
def team_note() -> str:
    """The team's note, for people the realm signed in."""
    return "The team note says: the harbour lights are checked every Tuesday."


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=PORT, path="/mcp")
