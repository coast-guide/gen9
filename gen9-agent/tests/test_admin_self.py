"""An admin can't lock themselves out through the API (api/admin.py): disabling, demoting or
deleting their own account is refused with 409 before Keycloak is called. The web app hides these
for yourself (J6 in docs/plans/manual-e2e.md), so another program with an admin token is what
reaches them; the terminal can't, as it's never an admin (auth-architecture decision 11)."""

import httpx
import pytest
from fastapi import FastAPI

from gen9_agent.api import admin
from gen9_agent.auth import Principal
from gen9_agent.deps import get_keycloak_admin, get_session

pytestmark = pytest.mark.asyncio

ADA = Principal(sub="ada", client_id="gen9-ui", roles=frozenset({"gen9-admin"}))


class RefusingKeycloak:
    """Fails the test if the API gets as far as Keycloak."""

    def __getattr__(self, name: str):
        raise AssertionError(f"Keycloak's {name} was called")


def app() -> FastAPI:
    api = FastAPI()
    api.include_router(admin.router)
    requires_admin = admin.AdminPrincipal.__metadata__[0].dependency
    api.dependency_overrides[requires_admin] = lambda: ADA
    api.dependency_overrides[admin._recent_admin] = lambda: ADA
    api.dependency_overrides[get_keycloak_admin] = RefusingKeycloak
    api.dependency_overrides[get_session] = lambda: None
    return api


@pytest.mark.parametrize(
    "patch",
    [{"enabled": False}, {"is_admin": False}, {"enabled": False, "is_admin": False}],
)
async def test_an_admin_cant_disable_or_demote_themselves(patch: dict) -> None:
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.patch("/v1/admin/users/ada", json=patch)
    assert response.status_code == 409
    assert response.json()["detail"] == "You cannot disable or demote your own account"


async def test_an_admin_cant_delete_themselves_here() -> None:
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.delete("/v1/admin/users/ada")
    assert response.status_code == 409
    assert response.json()["detail"] == "Delete your own account from Settings."
