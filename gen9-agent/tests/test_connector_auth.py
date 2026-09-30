"""Signing in to a connector (connector_auth.py): discovery from a server's 401, and the
authorization request, against mock servers."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from gen9_agent import connector_auth
from gen9_agent.connector_auth import authorization, canonical, sign_in_needed
from gen9_agent.connectors import ConnectorError

pytestmark = pytest.mark.asyncio

MCP = "https://mcp.test/mcp"
AS = "https://auth.test/realms/gen9"


def metadata(**extra) -> dict:
    return {
        "issuer": AS,
        "authorization_endpoint": f"{AS}/protocol/openid-connect/auth",
        "token_endpoint": f"{AS}/protocol/openid-connect/token",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        **extra,
    }


def server(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return routes.get(f"{request.method} {request.url}", httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def public_hosts(monkeypatch):
    """The mock hosts don't resolve: let the guard pass them, recording what it was asked."""
    checked: list[str] = []

    async def check_url(url: str, allow_private: bool) -> str:
        checked.append(url)
        if "private" in url:
            raise ConnectorError(
                "That server is on a private network, which connectors can't reach."
            )
        return url

    monkeypatch.setattr(connector_auth, "check_url", check_url)
    return checked


async def test_a_server_that_answers_needs_no_sign_in():
    seen: list[str] = []
    async with server({f"POST {MCP}": httpx.Response(200, json={})}, seen) as http:
        assert await sign_in_needed(http, MCP, allow_private=False) is None


async def test_discovery_follows_the_challenge_to_the_authorization_server(
    public_hosts,
):
    seen: list[str] = []
    challenge = 'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource/mcp", scope="tools:read"'
    routes = {
        f"POST {MCP}": httpx.Response(401, headers={"WWW-Authenticate": challenge}),
        "GET https://mcp.test/.well-known/oauth-protected-resource/mcp": httpx.Response(
            200,
            json={
                "resource": MCP,
                "authorization_servers": [AS],
                "scopes_supported": ["tools:read", "tools:write"],
            },
        ),
        # RFC 8414's path-aware URL first; this server only has OIDC discovery
        "GET https://auth.test/.well-known/openid-configuration/realms/gen9": httpx.Response(
            200,
            json=metadata(scopes_supported=["openid", "offline_access", "tools:read"]),
        ),
    }
    async with server(routes, seen) as http:
        found = await sign_in_needed(http, MCP, allow_private=False)
    assert found is not None
    assert found.resource == MCP and found.issuer == AS
    # The challenge's scopes win over the metadata's, and a refresh token is asked for
    assert found.scope == "tools:read offline_access"
    assert seen[:4] == [
        f"POST {MCP}",
        "GET https://mcp.test/.well-known/oauth-protected-resource/mcp",
        "GET https://auth.test/.well-known/oauth-authorization-server/realms/gen9",
        "GET https://auth.test/.well-known/openid-configuration/realms/gen9",
    ]
    # Every URL the server named was checked, and so were the endpoints the person is sent to
    assert f"{AS}/protocol/openid-connect/auth" in public_hosts
    assert f"{AS}/protocol/openid-connect/token" in public_hosts


async def test_an_authorization_server_on_a_private_network_is_refused():
    seen: list[str] = []
    private = "https://private.internal/realms/x"
    routes = {
        f"POST {MCP}": httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}),
        "GET https://mcp.test/.well-known/oauth-protected-resource/mcp": httpx.Response(
            200, json={"resource": MCP, "authorization_servers": [private]}
        ),
    }
    async with server(routes, seen) as http:
        with pytest.raises(ConnectorError, match="private network"):
            await sign_in_needed(http, MCP, allow_private=False)
    assert not any("private.internal" in s for s in seen)


async def test_no_usable_metadata_says_so():
    seen: list[str] = []
    routes = {
        f"POST {MCP}": httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})
    }
    async with server(routes, seen) as http:
        with pytest.raises(ConnectorError, match="doesn't say where"):
            await sign_in_needed(http, MCP, allow_private=False)


async def test_the_authorization_request_carries_pkce_state_resource_and_scope():
    found = connector_auth.SignIn(
        resource=MCP,
        metadata=connector_auth.OAuthMetadata.model_validate(metadata()),
        issuer=AS,
        scope="tools:read offline_access",
    )
    request = authorization(
        found, "gen9-client", "https://gen9.test/settings/connectors/callback"
    )
    parts = urlsplit(request.url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert (
        f"{parts.scheme}://{parts.netloc}{parts.path}"
        == f"{AS}/protocol/openid-connect/auth"
    )
    assert query["response_type"] == "code" and query["client_id"] == "gen9-client"
    assert query["resource"] == MCP and query["scope"] == "tools:read offline_access"
    assert query["state"] == request.state and len(request.state) >= 43
    assert query["code_challenge_method"] == "S256"
    digest = hashlib.sha256(request.code_verifier.encode()).digest()
    assert query["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip(
        "="
    )
    # Two requests never share a state or a verifier
    again = authorization(found, "gen9-client", "https://gen9.test/cb")
    assert again.state != request.state and again.code_verifier != request.code_verifier


async def test_the_canonical_server_uri():
    assert canonical("HTTPS://MCP.Example.com/mcp/") == "https://mcp.example.com/mcp"
    assert canonical("https://mcp.example.com/") == "https://mcp.example.com"
    assert (
        canonical("https://mcp.example.com/server/mcp#x")
        == "https://mcp.example.com/server/mcp"
    )
    json.dumps(canonical("https://mcp.example.com:8443"))


def signed(**extra) -> connector_auth.SignIn:
    return connector_auth.SignIn(
        resource=MCP,
        metadata=connector_auth.OAuthMetadata.model_validate(metadata(**extra)),
        issuer=AS,
        scope="tools:read",
    )


REDIRECT = "https://gen9.test/settings/connectors/callback"


async def test_registration_follows_the_spec_order():
    seen: list[str] = []
    register = f"{AS}/clients-registrations/openid-connect"
    routes = {
        f"POST {register}": httpx.Response(
            201, json={"client_id": "dyn-1", "redirect_uris": [REDIRECT]}
        ),
    }
    async with server(routes, seen) as http:
        # A client the operator registered for this issuer comes first
        pre = await connector_auth.register(
            http,
            signed(registration_endpoint=register),
            REDIRECT,
            False,
            preregistered={AS: ("gen9", "s3cret")},
            metadata_url=None,
        )
        # Then CIMD, when Gen9 has a public https URL and the server takes it
        cimd = await connector_auth.register(
            http,
            signed(
                registration_endpoint=register,
                client_id_metadata_document_supported=True,
            ),
            REDIRECT,
            False,
            preregistered={},
            metadata_url="https://gen9.example/oauth/client.json",
        )
        # A local Gen9 (no https URL) falls back to DCR, at the advertised endpoint only
        dcr = await connector_auth.register(
            http,
            signed(
                registration_endpoint=register,
                client_id_metadata_document_supported=True,
            ),
            REDIRECT,
            False,
            preregistered={},
            metadata_url="http://localhost:14000/oauth/client.json",
        )
        with pytest.raises(ConnectorError, match="doesn't let Gen9 register"):
            await connector_auth.register(
                http, signed(), REDIRECT, False, preregistered={}, metadata_url=None
            )
    assert (pre.how, pre.client_id, pre.client_secret) == (
        "pre-registered",
        "gen9",
        "s3cret",
    )
    assert (cimd.how, cimd.client_id) == (
        "cimd",
        "https://gen9.example/oauth/client.json",
    )
    assert (dcr.how, dcr.client_id, dcr.client_secret) == ("dcr", "dyn-1", None)
    assert seen == [f"POST {register}"]


async def test_the_callback_issuer_follows_rfc_9207():
    connector_auth.check_issuer(AS, AS, advertised=True)
    connector_auth.check_issuer(AS, AS, advertised=False)
    connector_auth.check_issuer(None, AS, advertised=False)
    for iss, advertised in (
        (None, True),
        (AS + "/", True),
        ("https://evil.test", False),
    ):
        with pytest.raises(ConnectorError):
            connector_auth.check_issuer(iss, AS, advertised=advertised)


async def test_the_code_and_the_refresh_token_are_exchanged_for_this_resource():
    sent: list[dict] = []
    token = f"{AS}/protocol/openid-connect/token"

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "url": str(request.url),
                "form": parse_qs(request.content.decode()),
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(
            200,
            json={
                "access_token": "at",
                "token_type": "Bearer",
                "expires_in": 300,
                "refresh_token": "rt",
            },
        )

    public = connector_auth.Registration("gen9-public", None, "dcr")
    confidential = connector_auth.Registration("gen9", "s3cret", "pre-registered")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        got = await connector_auth.exchange(
            http, signed(), public, "the-code", "the-verifier", REDIRECT, False
        )
        await connector_auth.refresh(http, signed(), confidential, "rt", False)
    assert (got.access_token, got.refresh_token, got.expires_in) == ("at", "rt", 300)
    assert [s["url"] for s in sent] == [token, token]
    code, renewal = (s["form"] for s in sent)
    assert code["grant_type"] == ["authorization_code"] and code["code_verifier"] == [
        "the-verifier"
    ]
    assert (
        code["resource"] == [MCP]
        and code["client_id"] == ["gen9-public"]
        and code["redirect_uri"] == [REDIRECT]
    )
    assert sent[0]["auth"] is None
    assert renewal["grant_type"] == ["refresh_token"] and renewal["resource"] == [MCP]
    assert sent[1]["auth"].startswith("Basic ")


async def test_a_refused_token_request_says_so():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": "invalid_grant"})
        )
    ) as http:
        with pytest.raises(ConnectorError, match="refused"):
            await connector_auth.refresh(
                http,
                signed(),
                connector_auth.Registration("c", None, "dcr"),
                "old",
                False,
            )


async def test_revocation_sends_the_refresh_token_then_the_access_token():
    sent: list[dict] = []
    endpoint = f"{AS}/protocol/openid-connect/revoke"

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "url": str(request.url),
                "form": parse_qs(request.content.decode()),
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(200)

    confidential = connector_auth.Registration("gen9", "s3cret", "pre-registered")
    public = connector_auth.Registration("gen9-public", None, "dcr")
    tokens = {"access_token": "at", "refresh_token": "rt"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with_endpoint = signed(revocation_endpoint=endpoint)
        assert await connector_auth.revoke(
            http, with_endpoint, confidential, tokens, False
        ) == ["refresh_token", "access_token"]
        # A public client names itself; one without a refresh token revokes its access token
        assert await connector_auth.revoke(
            http, with_endpoint, public, {"access_token": "at2"}, False
        ) == ["access_token"]
        # A server that offers no revocation: nothing sent
        assert await connector_auth.revoke(http, signed(), public, tokens, False) == []
    assert [s["url"] for s in sent] == [endpoint] * 3
    first, second, third = (s["form"] for s in sent)
    assert first == {
        "token": ["rt"],
        "token_type_hint": ["refresh_token"],
        "client_id": ["gen9"],
    }
    assert second["token"] == ["at"] and second["token_type_hint"] == ["access_token"]
    assert sent[0]["auth"].startswith("Basic ") and sent[1]["auth"].startswith("Basic ")
    assert third == {
        "token": ["at2"],
        "token_type_hint": ["access_token"],
        "client_id": ["gen9-public"],
    }
    assert sent[2]["auth"] is None


async def test_a_refused_revocation_says_so_and_a_private_endpoint_is_never_asked(
    public_hosts,
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503))
    ) as http:
        public = connector_auth.Registration("gen9-public", None, "dcr")
        with pytest.raises(ConnectorError, match="didn't revoke the refresh token"):
            await connector_auth.revoke(
                http,
                signed(revocation_endpoint=f"{AS}/revoke"),
                public,
                {"refresh_token": "rt"},
                False,
            )
        with pytest.raises(ConnectorError, match="private network"):
            await connector_auth.revoke(
                http,
                signed(revocation_endpoint="https://private.example/revoke"),
                public,
                {"refresh_token": "rt"},
                False,
            )
    assert public_hosts[-1] == "https://private.example/revoke"
