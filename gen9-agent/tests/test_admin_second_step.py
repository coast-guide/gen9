"""Admins need a second step (api/admin.py; gen9-learn plan, M10). Keycloak asks for it when an
admin signs in, so someone made admin while signed in, with no second step, is signed out and sets
one up at their next sign-in. Someone who has one keeps their sessions, and a demotion signs no one
out. The audit event says who was signed out."""

import httpx
import pytest
from fastapi import FastAPI

from gen9_agent.api import admin
from gen9_agent.auth import Principal
from gen9_agent.deps import get_keycloak_admin, get_session

pytestmark = pytest.mark.asyncio

ADA = Principal(sub="ada", client_id="gen9-ui", roles=frozenset({"gen9-admin"}))


class Keycloak:
    """Records the calls; the person holds `types` of credentials."""

    def __init__(self, types: list[str]) -> None:
        self.types = types
        self.calls: list[str] = []

    async def set_admin(self, user_id: str, is_admin: bool) -> None:
        self.calls.append(f"set_admin {is_admin}")

    async def credentials(self, user_id: str) -> list[dict]:
        self.calls.append("credentials")
        return [{"id": t, "type": t} for t in self.types]

    async def logout(self, user_id: str) -> None:
        self.calls.append("logout")


async def patch(
    monkeypatch, types: list[str], is_admin: bool
) -> tuple[list[str], dict]:
    keycloak = Keycloak(types)
    recorded: dict = {}

    async def record(request, actor, action, *, target=None, detail=None, **kwargs):
        recorded.update(detail or {})

    monkeypatch.setattr(admin.audit, "record", record)
    api = FastAPI()
    api.include_router(admin.router)
    requires_admin = admin.AdminPrincipal.__metadata__[0].dependency
    api.dependency_overrides[requires_admin] = lambda: ADA
    api.dependency_overrides[get_keycloak_admin] = lambda: keycloak
    api.dependency_overrides[get_session] = lambda: None
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.patch(
            "/v1/admin/users/mary", json={"is_admin": is_admin}
        )
    assert response.status_code == 204
    return keycloak.calls, recorded


@pytest.mark.parametrize("types", [[], ["password"]])
async def test_made_admin_without_a_second_step_is_signed_out(
    monkeypatch, types: list[str]
) -> None:
    calls, recorded = await patch(monkeypatch, types, is_admin=True)
    assert calls == ["set_admin True", "credentials", "logout"]
    assert recorded == {"admin": True, "signed_out": True}


@pytest.mark.parametrize(
    "second_step", ["otp", "recovery-authn-codes", "webauthn-passwordless"]
)
async def test_made_admin_with_a_second_step_stays_signed_in(
    monkeypatch, second_step: str
) -> None:
    calls, recorded = await patch(monkeypatch, ["password", second_step], is_admin=True)
    assert calls == ["set_admin True", "credentials"]
    assert recorded == {"admin": True}


async def test_a_demotion_signs_no_one_out(monkeypatch) -> None:
    calls, recorded = await patch(monkeypatch, ["password"], is_admin=False)
    assert calls == ["set_admin False"]
    assert recorded == {"admin": False}
