"""Resource-server side of OAuth 2.0: validate Keycloak access tokens locally.

Checks signature (JWKS, cached; refetched on an unknown `kid`, at most once per 30 s: PyJWT's
cooldown, so a rotated key is published passive first, docs/secrets.md), `iss`, `aud`, `exp`/`nbf`/`iat`,
that it is an access token (`typ: Bearer`, not an ID token) and that the calling client (`azp`)
is allowed. Errors follow RFC 6750 §3 (`WWW-Authenticate: Bearer error=...`).
"""

import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

REALM = "gen9-agent"
ALGORITHMS = ["RS256"]  # Keycloak's default realm key; never accept "none" or HMAC


@dataclass(frozen=True)
class Principal:
    sub: str
    client_id: str
    email: str | None = None
    name: str | None = None
    session_id: str | None = None
    roles: frozenset[str] = frozenset()
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    def has_role(self, role: str) -> bool:
        return role in self.roles


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        allowed_clients: frozenset[str] | None,
        keys: SigningKeySource,
        leeway_seconds: int = 10,
        required_scope: str | None = None,
    ) -> None:
        """`allowed_clients`: the `azp` values it takes; None for any client of the realm (agents
        that register themselves, a2a_server.py). `required_scope`: a scope the token must carry."""
        self.issuer = issuer
        self.audience = audience
        self.allowed_clients = allowed_clients
        self.required_scope = required_scope
        self.keys = keys
        self.leeway = leeway_seconds

    @classmethod
    def from_jwks_url(cls, jwks_url: str, **kwargs: Any) -> "TokenVerifier":
        # PyJWKClient caches the key set (5 min) and refetches when a token names an unknown kid,
        # at most once per 30 s (its cooldown_duration, a guard against forged kids hammering Keycloak)
        return cls(
            keys=jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5),
            **kwargs,
        )

    def verify(self, token: str) -> Principal:
        """Blocking (may fetch JWKS): call through a thread pool from async code."""
        signing_key = self.keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=self.audience,
            issuer=self.issuer,
            leeway=self.leeway,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        if claims.get("typ") != "Bearer":
            raise jwt.InvalidTokenError("not an access token")
        client_id = str(claims.get("azp") or "")
        if self.allowed_clients is not None and client_id not in self.allowed_clients:
            raise jwt.InvalidTokenError(
                "token was issued to a client that may not call this API"
            )
        if (
            self.required_scope
            and self.required_scope not in str(claims.get("scope", "")).split()
        ):
            raise jwt.InvalidTokenError(f"token lacks the scope {self.required_scope}")
        return Principal(
            sub=claims["sub"],
            client_id=client_id,
            email=claims.get("email"),
            name=claims.get("name"),
            session_id=claims.get("sid"),
            roles=frozenset(claims.get("realm_access", {}).get("roles", [])),
            claims=claims,
        )


bearer = HTTPBearer(
    auto_error=False, description="Keycloak access token with aud=gen9-agent"
)


def _unauthorized(
    error: str | None = None, description: str | None = None, max_age: int | None = None
) -> HTTPException:
    challenge = f'Bearer realm="{REALM}"'
    if error:
        challenge += f', error="{error}"'
    if description:
        challenge += f', error_description="{description}"'
    if max_age is not None:
        challenge += f', max_age="{max_age}"'
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail=description or "Authentication required",
        headers={"WWW-Authenticate": challenge},
    )


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
) -> Principal:
    if credentials is None:
        raise _unauthorized()
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        principal = await run_in_threadpool(verifier.verify, credentials.credentials)
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unreachable"
        ) from exc
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("invalid_token", "The access token expired") from exc
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise _unauthorized("invalid_token", "The access token is invalid") from exc
    # Who the request is, for the audit of anything refused later (audit.py)
    request.state.actor = principal.sub
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def forbidden(role: str) -> HTTPException:
    """403 with RFC 6750's insufficient_scope: the caller lacks a realm role."""
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=f"Requires role {role}",
        headers={
            "WWW-Authenticate": f'Bearer realm="{REALM}", error="insufficient_scope"'
        },
    )


def require_role(role: str):
    """Dependency factory: 403 (RFC 6750 insufficient_scope) unless the caller has a realm role."""

    async def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(role):
            raise forbidden(role)
        return principal

    return dependency


def require_recent_authentication(max_age: int):
    """Dependency factory for sensitive actions: the user must have signed in (entered a password,
    passkey...) within `max_age` seconds, per the token's `auth_time`. Otherwise 401 with RFC 9470's
    step-up challenge, so the client can ask for a fresh sign-in (OIDC max_age)."""

    async def dependency(principal: CurrentPrincipal) -> Principal:
        auth_time = principal.claims.get("auth_time")
        if not isinstance(auth_time, int) or time.time() - auth_time > max_age:
            raise _unauthorized(
                "insufficient_user_authentication",
                "Sign in again to continue",
                max_age=max_age,
            )
        return principal

    return dependency
