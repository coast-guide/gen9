"""Sign-in for the terminal: OAuth 2.0 Device Authorization Grant (RFC 8628) against Keycloak.

The user approves on any browser (the Gen9 sign-in page); this process polls for tokens. Tokens
are kept in a file only the user can read. Keycloak rotates refresh tokens, so every refresh
saves the new one. Signing out revokes this client's refresh token (RFC 7009), which ends the
CLI's access without signing the browser out.

Async throughout (AGENTS.md): HTTP goes through `httpx.AsyncClient`, and the token file, which
has no async API in the standard library, is read and written in a thread.
"""

import asyncio
import json
import os
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

CLIENT_ID = "gen9-cli"


class SignInRequired(Exception):
    """No saved sign-in, or Keycloak ended it (signed out, expired, account removed)."""


class SignInFailed(Exception):
    """The device sign-in was denied or timed out."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds

    @classmethod
    def from_response(cls, body: dict, now: float) -> "Tokens":
        return cls(
            body["access_token"], body["refresh_token"], now + body["expires_in"]
        )


def config_dir() -> Path:
    return Path(os.environ.get("GEN9_CONFIG_DIR", Path.home() / ".config" / "gen9"))


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def _write_tokens(tokens: Tokens) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)  # 0700
    # Create with 0600 from the start, so the refresh token is never readable by others
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(asdict(tokens), file)
    path.chmod(0o600)


def _read_tokens() -> Tokens:
    try:
        return Tokens(**json.loads(credentials_path().read_text()))
    except FileNotFoundError:
        raise SignInRequired from None


async def save_tokens(tokens: Tokens) -> None:
    await asyncio.to_thread(_write_tokens, tokens)


async def load_tokens() -> Tokens:
    return await asyncio.to_thread(_read_tokens)


async def forget_tokens() -> None:
    await asyncio.to_thread(credentials_path().unlink, missing_ok=True)


class Keycloak:
    """The realm's OIDC endpoints, from discovery (`await Keycloak.discover(...)`)."""

    def __init__(self, http: httpx.AsyncClient, endpoints: dict) -> None:
        self.http = http
        self.endpoints = endpoints

    @classmethod
    async def discover(cls, issuer: str, http: httpx.AsyncClient) -> "Keycloak":
        metadata = await http.get(
            f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        )
        metadata.raise_for_status()
        return cls(http, metadata.json())

    async def device_sign_in(
        self,
        show: Callable[[dict], None],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> Tokens:
        """RFC 8628 §3.1-3.5: get a user code, show it, poll the token endpoint."""
        response = await self.http.post(
            self.endpoints["device_authorization_endpoint"],
            data={"client_id": CLIENT_ID, "scope": "openid"},
        )
        response.raise_for_status()
        grant = response.json()
        show(grant)
        interval = grant.get("interval", 5)
        deadline = clock() + grant["expires_in"]
        while clock() < deadline:
            await sleep(interval)
            token = await self.http.post(
                self.endpoints["token_endpoint"],
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": grant["device_code"],
                    "client_id": CLIENT_ID,
                },
            )
            body = token.json()
            if token.status_code == 200:
                return Tokens.from_response(body, clock())
            error = body.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":  # §3.5: back off by 5 seconds
                interval += 5
                continue
            if error == "access_denied":
                raise SignInFailed("Sign-in was denied.")
            if error == "expired_token":
                break
            raise SignInFailed(
                body.get("error_description") or f"Sign-in failed ({error})."
            )
        raise SignInFailed("The code expired. Run `gen9 login` again.")

    async def refresh(
        self, tokens: Tokens, clock: Callable[[], float] = time.time
    ) -> Tokens:
        response = await self.http.post(
            self.endpoints["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": CLIENT_ID,
            },
        )
        # invalid_grant: the session ended, the user was removed…
        if response.status_code in (400, 401):
            await forget_tokens()
            raise SignInRequired
        response.raise_for_status()
        fresh = Tokens.from_response(response.json(), clock())
        await save_tokens(fresh)
        return fresh

    async def revoke(self, tokens: Tokens) -> None:
        response = await self.http.post(
            self.endpoints["revocation_endpoint"],
            data={
                "client_id": CLIENT_ID,
                "token": tokens.refresh_token,
                "token_type_hint": "refresh_token",
            },
        )
        response.raise_for_status()


async def access_token(
    keycloak: Keycloak, clock: Callable[[], float] = time.time
) -> str:
    """A valid access token: the saved one, or a refreshed one if it expires within 30 s."""
    tokens = await load_tokens()
    if tokens.expires_at - clock() < 30:
        tokens = await keycloak.refresh(tokens, clock)
    return tokens.access_token
