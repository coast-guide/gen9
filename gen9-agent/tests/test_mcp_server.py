"""Gen9 as an MCP server (mcp_server.py): the challenge and the Protected Resource Metadata an MCP
client starts from, and the words each run status is told in. Signing in and the tools on the real
stacks are e2e's (`e2e/mcp-server.mjs`)."""

import httpx
import pytest
from fastapi import FastAPI

from gen9_agent import mcp_server
from gen9_agent.models import ACTIVE_RUN_STATUSES, FINAL_RUN_STATUSES
from gen9_agent.settings import Settings

pytestmark = pytest.mark.asyncio

SETTINGS = Settings.model_construct(
    keycloak_issuer="http://localhost:15000/realms/gen9",
    keycloak_internal_url="http://gen9-keycloak:8080",
    gen9_api_public_url="http://localhost:17000",
    gen9_ui_url="http://localhost:14000",
    mcp_ask_wait_s=120,
    mcp_allowed_origins=[],
)


async def test_a_client_without_a_token_is_told_where_to_sign_in() -> None:
    app = FastAPI()
    mcp_app = mcp_server.asgi(app, SETTINGS)
    app.mount("/", mcp_app)
    async with (
        mcp_app.lifespan(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:17000"
        ) as http,
    ):
        bare = await http.post("/mcp", json={})
        metadata = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert bare.status_code == 401
    challenge = bare.headers["www-authenticate"]
    assert 'scope="gen9-mcp"' in challenge
    assert (
        'resource_metadata="http://localhost:17000/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert metadata["resource"] == "http://localhost:17000/mcp"
    assert metadata["authorization_servers"] == ["http://localhost:15000/realms/gen9"]
    assert metadata["scopes_supported"] == ["gen9-mcp"]


async def origins_answered(
    settings: Settings, base_url: str, origins: list[str]
) -> list[int]:
    """The status `/mcp` answers a request without a token from each origin (None: no Origin)."""
    app = FastAPI()
    mcp_app = mcp_server.asgi(app, settings)
    app.mount("/", mcp_app)
    async with (
        mcp_app.lifespan(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base_url
        ) as http,
    ):
        return [
            (
                await http.post("/mcp", json={}, headers={"Origin": o} if o else {})
            ).status_code
            for o in origins
        ]


async def test_a_page_from_another_origin_is_refused_before_its_token_is_read() -> None:
    # A local install: no Origin (a client outside a browser) and loopback pages go on to the
    # token check (401); a rebinding page keeps its own name as its Origin, and gets 403
    assert await origins_answered(
        SETTINGS,
        "http://localhost:17000",
        [
            None,
            "http://localhost:17000",
            "http://localhost:6274",
            "http://evil.example",
            "null",
        ],
    ) == [401, 401, 401, 403, 403]


async def test_behind_a_public_name_only_its_own_and_the_operators_origins_go_on() -> (
    None
):
    public = SETTINGS.model_copy(
        update={
            "gen9_api_public_url": "https://gen9.example",
            "mcp_allowed_origins": ["https://inspector.example"],
        }
    )
    assert mcp_server.origins(public) == [
        "https://gen9.example",
        "https://inspector.example",
    ]
    assert await origins_answered(
        public,
        "https://gen9.example",
        [
            None,
            "https://gen9.example",
            "https://inspector.example",
            "http://localhost:6274",
        ],
    ) == [401, 401, 401, 403]


async def test_its_tokens_are_checked_for_this_server() -> None:
    verifier = mcp_server.auth(SETTINGS).token_verifier
    # Keys from Keycloak's address inside the network; issuer and audience as tokens carry them
    assert (
        verifier.jwks_uri
        == "http://gen9-keycloak:8080/realms/gen9/protocol/openid-connect/certs"
    )
    assert verifier.issuer == "http://localhost:15000/realms/gen9"
    assert verifier.audience == "http://localhost:17000/mcp"
    assert verifier.required_scopes == ["gen9-mcp"]


async def test_every_run_status_has_its_word() -> None:
    assert set(mcp_server.SAID) == set(ACTIVE_RUN_STATUSES) | set(FINAL_RUN_STATUSES)
