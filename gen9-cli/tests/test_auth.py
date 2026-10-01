"""Device sign-in (RFC 8628 polling rules), refresh, and where tokens are kept."""

import json
import stat

import httpx2
import pytest

from gen9_cli import auth
from gen9_cli.auth import Keycloak, SignInFailed, SignInRequired, Tokens

ISSUER = "http://kc.test/realms/gen9"
DISCOVERY = {
    "device_authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth/device",
    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
    "revocation_endpoint": f"{ISSUER}/protocol/openid-connect/revoke",
}
GRANT = {
    "device_code": "dc",
    "user_code": "ABCD-EFGH",
    "verification_uri": f"{ISSUER}/device",
    "verification_uri_complete": f"{ISSUER}/device?user_code=ABCD-EFGH",
    "expires_in": 600,
    "interval": 5,
}
TOKENS = {"access_token": "at", "refresh_token": "rt", "expires_in": 300}

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GEN9_CONFIG_DIR", str(tmp_path / "gen9"))
    return tmp_path / "gen9"


async def keycloak(
    token_responses: list[tuple[int, dict]],
) -> tuple[Keycloak, list[dict]]:
    """A Keycloak whose token endpoint answers with `token_responses`, in order."""
    posted: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/openid-configuration"):
            return httpx2.Response(200, json=DISCOVERY)
        if request.url.path.endswith("/auth/device"):
            return httpx2.Response(200, json=GRANT)
        posted.append(dict(httpx2.QueryParams(request.content.decode())))
        status, body = token_responses.pop(0)
        return httpx2.Response(status, json=body)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return await Keycloak.discover(ISSUER, http), posted


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_polls_until_approved_and_backs_off_on_slow_down():
    kc, posted = await keycloak(
        [
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, TOKENS),
        ]
    )
    shown, clock = [], Clock()
    tokens = await kc.device_sign_in(shown.append, sleep=clock.sleep, clock=clock)
    assert shown == [GRANT]
    assert clock.slept == [5, 5, 10]  # slow_down adds 5 seconds (RFC 8628 §3.5)
    assert tokens == Tokens("at", "rt", clock.now + 300)
    assert {p["grant_type"] for p in posted} == {
        "urn:ietf:params:oauth:grant-type:device_code"
    }
    assert {p["client_id"] for p in posted} == {"gen9-cli"}


async def test_denied_sign_in():
    kc, _ = await keycloak([(400, {"error": "access_denied"})])
    clock = Clock()
    with pytest.raises(SignInFailed, match="denied"):
        await kc.device_sign_in(lambda grant: None, sleep=clock.sleep, clock=clock)


async def test_code_expires():
    kc, _ = await keycloak([(400, {"error": "expired_token"})])
    clock = Clock()
    with pytest.raises(SignInFailed, match="expired"):
        await kc.device_sign_in(lambda grant: None, sleep=clock.sleep, clock=clock)


async def test_refresh_saves_rotated_token_and_ended_session_forgets_it(config_dir):
    kc, _ = await keycloak(
        [(200, {**TOKENS, "refresh_token": "rt2"}), (400, {"error": "invalid_grant"})]
    )
    await auth.save_tokens(Tokens("old", "rt", 0))
    assert await auth.access_token(kc) == "at"
    assert (
        await auth.load_tokens()
    ).refresh_token == "rt2"  # Keycloak rotates refresh tokens
    await auth.save_tokens(Tokens("old", "rt2", 0))
    with pytest.raises(SignInRequired):
        await auth.access_token(kc)
    assert not (config_dir / "credentials.json").exists()


async def test_tokens_are_readable_only_by_the_user(config_dir):
    await auth.save_tokens(Tokens("at", "rt", 1.0))
    assert stat.S_IMODE((config_dir / "credentials.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert (
        json.loads((config_dir / "credentials.json").read_text())["refresh_token"]
        == "rt"
    )
