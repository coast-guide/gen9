"""Admin routes ask Keycloak whether the caller is an admin now (deps.require_admin): a token
keeps its roles for 5 minutes, and access removed in that time must stop at once (OWASP ASVS 5.0
8.3.2). Seen live: Quinn, demoted, kept admin until their token was refreshed."""

import httpx
import pytest
from fastapi import FastAPI

from gen9_agent.auth import Principal, get_principal
from gen9_agent.deps import AdminPrincipal, get_keycloak_admin
from gen9_agent.keycloak_admin import KeycloakAdminError

pytestmark = pytest.mark.asyncio


class Keycloak:
    def __init__(self, roles: set[str] | Exception) -> None:
        self.roles = roles
        self.asked: list[str] = []

    async def realm_roles(self, user_id: str) -> set[str]:
        self.asked.append(user_id)
        if isinstance(self.roles, Exception):
            raise self.roles
        return self.roles


async def call(token_roles: set[str], keycloak: Keycloak) -> httpx.Response:
    app = FastAPI()

    @app.get("/admin-only")
    async def admin_only(principal: AdminPrincipal) -> str:
        return principal.sub

    app.dependency_overrides[get_principal] = lambda: Principal(
        sub="quinn", client_id="gen9-ui", roles=frozenset(token_roles)
    )
    app.dependency_overrides[get_keycloak_admin] = lambda: keycloak
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        return await client.get("/admin-only")


async def test_an_admin_in_the_token_and_in_keycloak_passes() -> None:
    keycloak = Keycloak({"gen9-user", "gen9-admin"})
    response = await call({"gen9-user", "gen9-admin"}, keycloak)
    assert response.status_code == 200 and response.json() == "quinn"
    assert keycloak.asked == ["quinn"]


async def test_admin_access_removed_since_the_token_was_issued_is_refused() -> None:
    response = await call({"gen9-user", "gen9-admin"}, Keycloak({"gen9-user"}))
    assert response.status_code == 403
    assert response.json()["detail"] == "Requires role gen9-admin"
    assert 'error="insufficient_scope"' in response.headers["WWW-Authenticate"]


async def test_a_token_without_the_role_is_refused_before_asking_keycloak() -> None:
    keycloak = Keycloak({"gen9-admin"})
    response = await call({"gen9-user"}, keycloak)
    assert response.status_code == 403 and keycloak.asked == []


async def test_a_user_keycloak_no_longer_has_is_refused() -> None:
    response = await call(
        {"gen9-admin"}, Keycloak(KeycloakAdminError(404, "Not found"))
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "failure",
    [KeycloakAdminError(502, "Keycloak returned 500"), httpx.ConnectError("refused")],
)
async def test_when_keycloak_cant_answer_the_request_is_refused(
    failure: Exception,
) -> None:
    response = await call({"gen9-admin"}, Keycloak(failure))
    assert response.status_code == 503
