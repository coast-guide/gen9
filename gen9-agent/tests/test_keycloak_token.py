"""gen9-agent's service-account token (keycloak_admin.py), which it shows Temporal and Keycloak's Admin
API, is judged by wall time, as its expiry is: a host that slept paused Docker's VM, whose monotonic
clock then lagged, and an expired token looked fresh for minutes (M9)."""

from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from gen9_agent import keycloak_admin
from gen9_agent.keycloak_admin import KeycloakAdmin

pytestmark = pytest.mark.asyncio


def admin(issued: list[int]) -> KeycloakAdmin:
    def handler(request: httpx.Request) -> httpx.Response:
        issued.append(1)
        return httpx.Response(
            200, json={"access_token": f"token-{len(issued)}", "expires_in": 300}
        )

    settings = SimpleNamespace(
        keycloak_admin_client_secret=SecretStr("s"),
        keycloak_admin_client_id="gen9-agent",
        keycloak_base_url="http://keycloak",
        realm="gen9",
    )
    return KeycloakAdmin(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )  # ty: ignore[invalid-argument-type]


async def test_a_token_is_reused_while_it_stays_valid(monkeypatch) -> None:
    now = [1_000_000.0]
    monkeypatch.setattr(keycloak_admin.time, "time", lambda: now[0])
    issued: list[int] = []
    kc = admin(issued)
    assert await kc.access_token() == "token-1"
    now[0] += 200  # 100 s left: still more than the 30 asked for
    assert await kc.access_token() == "token-1"
    assert await kc.access_token(min_valid_s=120) == "token-2"  # less than 120 left
    assert len(issued) == 2


async def test_after_the_host_slept_the_token_is_renewed_at_once(monkeypatch) -> None:
    """The VM's monotonic clock stood still while the host slept; wall time moved on 10 minutes."""
    wall = [1_000_000.0]
    monkeypatch.setattr(keycloak_admin.time, "time", lambda: wall[0])
    monkeypatch.setattr(keycloak_admin.time, "monotonic", lambda: 500.0)
    issued: list[int] = []
    kc = admin(issued)
    assert await kc.access_token() == "token-1"
    wall[0] += 600
    assert await kc.access_token() == "token-2"
