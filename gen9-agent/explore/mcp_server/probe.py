"""Probe: FastMCP 4 as Gen9's MCP server, mounted in a FastAPI app, trusting Gen9's Keycloak.

Run (with a token from the CLI's device flow in $TOKEN_FILE, audience gen9-agent):
    uv run python explore/mcp_server/probe.py

Shows: the routes FastMCP adds, the 401 challenge, the Protected Resource Metadata, a tool
seeing the token's person, a token for another audience refused, and FastAPI's own routes
still answering.
"""

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from fastmcp.server.dependencies import get_access_token

REALM = "http://localhost:15000/realms/gen9"
BASE = "http://127.0.0.1:17999"


def server(audience: str) -> FastAPI:
    mcp = FastMCP(
        "Gen9",
        auth=KeycloakAuthProvider(realm_url=REALM, base_url=BASE, audience=audience),
    )

    @mcp.tool
    def whoami() -> dict:
        """Who the token says you are."""
        token = get_access_token()
        return {"sub": token.claims.get("sub") if token else None}

    mcp_app = mcp.http_app(path="/mcp", stateless_http=True)
    app = FastAPI(lifespan=mcp_app.lifespan)

    @app.get("/v1/ping")
    async def ping() -> dict:
        return {"ok": True}

    app.mount("/", mcp_app)
    print("routes:", [getattr(r, "path", r) for r in mcp_app.routes])
    return app


async def main() -> None:
    # Reading a file blocks: in a thread
    saved = await asyncio.to_thread(Path(os.environ["TOKEN_FILE"]).read_text)
    token = json.loads(saved)["access_token"]
    for audience, label in (
        ("gen9-agent", "right audience"),
        ("someone-else", "wrong audience"),
    ):
        config = uvicorn.Config(
            server(audience), host="127.0.0.1", port=17999, log_level="warning"
        )
        srv = uvicorn.Server(config)
        task = asyncio.create_task(srv.serve())
        for _ in range(200):  # uvicorn sets `started` once it listens
            if srv.started:
                break
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient() as http:
            print(label, "ping:", (await http.get(f"{BASE}/v1/ping")).json())
            bare = await http.post(f"{BASE}/mcp", json={})
            print(
                label,
                "no token:",
                bare.status_code,
                bare.headers.get("www-authenticate"),
            )
            prm = await http.get(f"{BASE}/.well-known/oauth-protected-resource/mcp")
            print(label, "metadata:", prm.status_code, prm.text[:300])
        try:
            async with Client(f"{BASE}/mcp", auth=BearerAuth(token)) as client:
                result = await client.call_tool("whoami", {})
                print(label, "whoami:", result.structured_content)
        except Exception as e:  # noqa: BLE001
            print(label, "refused:", type(e).__name__, str(e)[:200])
        srv.should_exit = True
        await task


with contextlib.suppress(KeyboardInterrupt):
    asyncio.run(main())
sys.exit(0)
