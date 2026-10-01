"""Signing a person out everywhere ends their offline sessions too (keycloak_admin.py): Keycloak's
logout leaves the offline token an agent asked for, and it went on refreshing (P7-B1)."""

from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from gen9_agent.keycloak_admin import KeycloakAdmin

pytestmark = pytest.mark.asyncio

ADMIN = "/admin/realms/gen9"


def keycloak(
    calls: list[str], consents: list[dict], gone: frozenset[str] = frozenset()
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        calls.append(
            f"{request.method} {path.removeprefix(ADMIN)}{'?' + request.url.query.decode() if request.url.query else ''}"
        )
        if path.endswith("/consents"):
            return httpx.Response(200, json=consents)
        if "/offline-sessions/" in path:
            client = path.rsplit("/", 1)[1]
            return httpx.Response(
                200, json=[{"id": f"{client}-s1"}, {"id": f"{client}-s2"}]
            )
        if request.method == "DELETE" and path.rsplit("/", 1)[1] in gone:
            return httpx.Response(404)
        return httpx.Response(204)

    settings = SimpleNamespace(
        keycloak_admin_client_secret=SecretStr("s"),
        keycloak_admin_client_id="gen9-agent",
        keycloak_base_url="http://keycloak",
        realm="gen9",
    )
    return KeycloakAdmin(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )  # ty: ignore[invalid-argument-type]


async def test_signing_out_ends_the_offline_sessions_of_each_client_holding_one() -> (
    None
):
    calls: list[str] = []
    consents = [
        # An agent with consent and an offline token, and one with an offline token only
        {
            "clientId": "gen9-mcp",
            "additionalGrants": [{"client": "uuid-mcp", "key": "Offline Token"}],
        },
        {
            "clientId": "https://agent.example/client.json",
            "additionalGrants": [{"client": "uuid-doc", "key": "Offline Token"}],
        },
        {"clientId": "gen9-cli", "additionalGrants": []},
    ]
    await keycloak(calls, consents).logout("u1")
    assert calls[0] == "POST /users/u1/logout"
    assert calls[1] == "GET /users/u1/consents"
    assert sorted(calls[2:]) == sorted(
        [
            "GET /users/u1/offline-sessions/uuid-mcp",
            "DELETE /sessions/uuid-mcp-s1?isOffline=true",
            "DELETE /sessions/uuid-mcp-s2?isOffline=true",
            "GET /users/u1/offline-sessions/uuid-doc",
            "DELETE /sessions/uuid-doc-s1?isOffline=true",
            "DELETE /sessions/uuid-doc-s2?isOffline=true",
        ]
    )


async def test_a_session_gone_meanwhile_is_no_failure() -> None:
    calls: list[str] = []
    consents = [
        {
            "clientId": "gen9-mcp",
            "additionalGrants": [{"client": "uuid-mcp", "key": "Offline Token"}],
        }
    ]
    await keycloak(calls, consents, gone=frozenset({"uuid-mcp-s1"})).logout("u1")
    assert "DELETE /sessions/uuid-mcp-s2?isOffline=true" in calls
