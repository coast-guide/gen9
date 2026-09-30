"""Signing in to a connector for a person: MCP's authorization (2026-07-28), as a web flow
(docs/plans/harness.md, "Secrets and OAuth per person").

The MCP SDK's client runs the redirect inside one process, so Gen9 adopts its parts and runs the
flow itself across the browser:
- **Discovery.** The server's 401 names its Protected Resource Metadata (RFC 9728), which names
  the authorization server; its metadata comes from RFC 8414 or OpenID Connect Discovery, tried in
  the SDK's order. Every URL a server names goes through the connectors' SSRF guard first.
- **The request.** PKCE (S256), a random `state`, `resource` (RFC 8707: the server's canonical
  URL), and the scopes the challenge asked for (else the metadata's). The verifier, the state and
  the expected issuer are what the callback needs, and they stay with Gen9, sealed.
- **Revocation.** When a connector goes, its tokens are revoked where the server offers it
  (RFC 7009); MCP itself says nothing about it.
"""

import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import httpx2
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    is_valid_client_metadata_url,
    should_use_client_metadata_url,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import ValidationError

from .connector_net import ConnectorError, Reach, check_url

# The request an MCP client opens with; a protected server answers it with 401
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2026-07-28",
        "capabilities": {},
        "clientInfo": {"name": "gen9", "version": "0"},
    },
}
MCP_ACCEPT = "application/json, text/event-stream"
GRANT_TYPES = ["authorization_code", "refresh_token"]


@dataclass(frozen=True)
class SignIn:
    """What a server that needs sign-in told Gen9: where to sign in, and with which scopes."""

    resource: str  # the server's canonical URL, sent as `resource`
    metadata: OAuthMetadata  # its authorization server's
    issuer: str  # as the authorization server's metadata states it, for the `iss` check
    scope: str | None  # the scopes to ask for, or None to ask for none


def canonical(url: str) -> str:
    """The server's canonical URI (RFC 8707, MCP's "Canonical Server URI"): lowercase scheme and
    host, no fragment, and no trailing slash unless the path is only "/"."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


async def _get_json(
    http: httpx.AsyncClient, url: str, allow_private: Reach | bool
) -> bytes | None:
    await check_url(url, allow_private)
    response = await http.get(url, headers={"Accept": "application/json"})
    return response.content if response.status_code == 200 else None


async def sign_in_needed(
    http: httpx.AsyncClient, url: str, allow_private: Reach | bool
) -> SignIn | None:
    """None when the server answers without sign-in; else where and how to sign in. Raises
    ConnectorError when it asks for sign-in but doesn't say where, in a way Gen9 can use."""
    await check_url(url, allow_private)
    response = await http.post(
        url,
        json=INITIALIZE,
        headers={"Accept": MCP_ACCEPT, "Content-Type": "application/json"},
    )
    if response.status_code != 401:
        return None
    # The MCP SDK's parsers take its own HTTP library's responses: the same status and headers
    challenge = httpx2.Response(401, headers=list(response.headers.multi_items()))
    resource: ProtectedResourceMetadata | None = None
    for candidate in build_protected_resource_metadata_discovery_urls(
        extract_resource_metadata_from_www_auth(challenge), url
    ):
        body = await _get_json(http, candidate, allow_private)
        if body is None:
            continue
        try:
            resource = ProtectedResourceMetadata.model_validate_json(body)
            break
        except ValidationError:
            continue
    authorization_server = (
        str(resource.authorization_servers[0])
        if resource and resource.authorization_servers
        else None
    )
    metadata: OAuthMetadata | None = None
    raw: dict = {}
    for candidate in build_oauth_authorization_server_metadata_discovery_urls(
        authorization_server, url
    ):
        body = await _get_json(http, candidate, allow_private)
        if body is None:
            continue
        try:
            metadata = OAuthMetadata.model_validate_json(body)
            raw = json.loads(body)
            break
        except ValidationError:
            continue
    if metadata is None:
        raise ConnectorError(
            "The server asks you to sign in, but doesn't say where in a way Gen9 can use."
        )
    # Every endpoint the person or Gen9 will be sent to must pass the same guard
    for endpoint in (metadata.authorization_endpoint, metadata.token_endpoint):
        await check_url(str(endpoint), allow_private)
    # The challenge's scopes, else the metadata's, and `offline_access` when the authorization
    # server offers it, since Gen9 keeps a refresh token
    scope = get_client_metadata_scopes(
        extract_scope_from_www_auth(challenge), resource, metadata, GRANT_TYPES
    )
    return SignIn(
        resource=canonical(str(resource.resource) if resource else url),
        metadata=metadata,
        # The issuer exactly as the metadata document states it (RFC 9207 compares strings)
        issuer=raw.get("issuer", str(metadata.issuer)),
        scope=scope,
    )


@dataclass(frozen=True)
class Authorization:
    """An authorization request: the URL to send the person to, and what its callback needs."""

    url: str
    state: str
    code_verifier: str


def authorization(sign_in: SignIn, client_id: str, redirect_uri: str) -> Authorization:
    """The authorization URL, with PKCE (S256), `state`, `resource` and the scopes."""
    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
        "resource": sign_in.resource,
    }
    if sign_in.scope:
        params["scope"] = sign_in.scope
    endpoint = str(sign_in.metadata.authorization_endpoint)
    joiner = "&" if urlsplit(endpoint).query else "?"
    return Authorization(
        url=f"{endpoint}{joiner}{urlencode(params)}",
        state=state,
        code_verifier=pkce.code_verifier,
    )


@dataclass(frozen=True)
class Registration:
    """Gen9 as a client of one authorization server."""

    client_id: str
    client_secret: str | None  # None for a public client, which PKCE alone protects
    how: str  # "pre-registered", "cimd" or "dcr"


def client_metadata(redirect_uri: str) -> OAuthClientMetadata:
    return OAuthClientMetadata.model_validate(
        {
            "client_name": "Gen9",
            "redirect_uris": [redirect_uri],
            "grant_types": GRANT_TYPES,
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )


async def register(
    http: httpx.AsyncClient,
    sign_in: SignIn,
    redirect_uri: str,
    allow_private: Reach | bool,
    *,
    preregistered: dict[str, tuple[str, str | None]],
    metadata_url: str | None,
) -> Registration:
    """Gen9's client at the server's authorization server, in the spec's order: a client the
    operator registered for this issuer; a Client ID Metadata Document, when Gen9 has a public
    https URL and the server takes them; else Dynamic Client Registration, only at the endpoint
    the server advertises. Raises ConnectorError when none applies."""
    if sign_in.issuer in preregistered:
        client_id, secret = preregistered[sign_in.issuer]
        return Registration(client_id, secret, "pre-registered")
    if is_valid_client_metadata_url(metadata_url) and should_use_client_metadata_url(
        sign_in.metadata, metadata_url
    ):
        return Registration(str(metadata_url), None, "cimd")
    endpoint = sign_in.metadata.registration_endpoint
    if endpoint is None:
        raise ConnectorError(
            "The server's sign-in doesn't let Gen9 register. An admin can register Gen9 there."
        )
    await check_url(str(endpoint), allow_private)
    body = client_metadata(redirect_uri).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    response = await http.post(str(endpoint), json=body)
    if response.status_code not in (200, 201):
        raise ConnectorError("The server's sign-in refused to register Gen9.")
    try:
        info = OAuthClientInformationFull.model_validate_json(response.content)
    except ValidationError as e:
        raise ConnectorError(
            "The server's sign-in answered in a way Gen9 can't use."
        ) from e
    if not info.client_id:
        raise ConnectorError("The server's sign-in answered in a way Gen9 can't use.")
    return Registration(info.client_id, info.client_secret, "dcr")


def check_issuer(iss: str | None, expected: str, advertised: bool) -> None:
    """RFC 9207 as MCP applies it: a present `iss` must equal the recorded issuer, compared as a
    plain string; an absent one is refused only when the server advertised that it sends it.
    Raises ConnectorError."""
    if iss is not None:
        if iss != expected:
            raise ConnectorError("The sign-in came back from the wrong server.")
        return
    if advertised:
        raise ConnectorError(
            "The sign-in came back without saying which server it was."
        )


async def _token(
    http: httpx.AsyncClient,
    sign_in: SignIn,
    registration: Registration,
    form: dict[str, str],
    allow_private: Reach | bool,
) -> OAuthToken:
    endpoint = str(sign_in.metadata.token_endpoint)
    await check_url(endpoint, allow_private)
    form = {**form, "client_id": registration.client_id, "resource": sign_in.resource}
    if registration.client_secret:
        # A confidential client (DCR may issue a secret): HTTP Basic, OAuth 2.1's default
        basic = httpx.BasicAuth(registration.client_id, registration.client_secret)
        response = await http.post(endpoint, data=form, auth=basic)
    else:
        response = await http.post(endpoint, data=form)
    if response.status_code != 200:
        raise ConnectorError("The server's sign-in refused Gen9's request for a token.")
    try:
        return OAuthToken.model_validate_json(response.content)
    except ValidationError as e:
        raise ConnectorError(
            "The server's sign-in answered in a way Gen9 can't use."
        ) from e


async def exchange(
    http: httpx.AsyncClient,
    sign_in: SignIn,
    registration: Registration,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    allow_private: Reach | bool,
) -> OAuthToken:
    """The authorization code for tokens, with the PKCE verifier and `resource`."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    return await _token(http, sign_in, registration, form, allow_private)


async def refresh(
    http: httpx.AsyncClient,
    sign_in: SignIn,
    registration: Registration,
    refresh_token: str,
    allow_private: Reach | bool,
) -> OAuthToken:
    """A new access token from the refresh token, for the same `resource`."""
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    return await _token(http, sign_in, registration, form, allow_private)


async def revoke(
    http: httpx.AsyncClient,
    sign_in: SignIn,
    registration: Registration,
    tokens: dict[str, str | None],
    allow_private: Reach | bool,
) -> list[str]:
    """RFC 7009: the refresh token, then the access token, revoked at the endpoint the server's
    metadata names (`revocation_endpoint`), the client authenticated as at the token endpoint.
    The kinds revoked; none when the server offers no revocation. Raises ConnectorError when it
    refuses, for the caller to log: the tokens are deleted either way."""
    endpoint = sign_in.metadata.revocation_endpoint
    if endpoint is None:
        return []
    await check_url(str(endpoint), allow_private)
    auth = (
        httpx.BasicAuth(registration.client_id, registration.client_secret)
        if registration.client_secret
        else httpx.USE_CLIENT_DEFAULT
    )
    revoked = []
    # The refresh token first: revoking it SHOULD also end the access tokens of its grant
    for kind in ("refresh_token", "access_token"):
        token = tokens.get(kind)
        if not token:
            continue
        form = {
            "token": token,
            "token_type_hint": kind,
            "client_id": registration.client_id,
        }
        response = await http.post(str(endpoint), data=form, auth=auth)
        # 200 also for a token the server no longer knows (RFC 7009, 2.2)
        if response.status_code != 200:
            raise ConnectorError(
                f"The server's sign-in didn't revoke the {kind.replace('_', ' ')} "
                f"(HTTP {response.status_code})."
            )
        revoked.append(kind)
    return revoked


def kept(sign_in: dict) -> tuple[SignIn, Registration]:
    """The sign-in kept on a connector (`connectors.sign_in`), and Gen9's client there, without
    its secret, which is sealed apart."""
    return (
        SignIn(
            resource=sign_in["resource"],
            metadata=OAuthMetadata.model_validate(sign_in["metadata"]),
            issuer=sign_in["issuer"],
            scope=sign_in.get("scope"),
        ),
        Registration(sign_in["client_id"], None, sign_in["how"]),
    )


def tokens_json(token: OAuthToken, previous_refresh: str | None = None) -> str:
    """What is sealed of a token response. A refresh that doesn't return a new refresh token
    keeps the one it used."""
    return json.dumps(
        {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token or previous_refresh,
            "expires_at": time.time() + token.expires_in if token.expires_in else None,
            "scope": token.scope,
        }
    )
