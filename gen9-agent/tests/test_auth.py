"""TokenVerifier accepts only well-formed Keycloak access tokens for this API."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from gen9_agent.auth import TokenVerifier

ISSUER = "http://localhost:15000/realms/gen9"


class StaticKeys:
    """Stands in for PyJWKClient: resolves every token to one public key."""

    def __init__(self, public_key) -> None:
        self.jwk = jwt.PyJWK.from_dict(
            jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
            | {"kid": "k1", "alg": "RS256"}
        )

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        return self.jwk


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def verifier(keypair) -> TokenVerifier:
    return TokenVerifier(
        issuer=ISSUER,
        audience="gen9-agent",
        allowed_clients=frozenset({"gen9-ui"}),
        keys=StaticKeys(keypair[1]),
    )


def make_token(private_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": ["gen9-agent", "account"],
        "sub": "user-1",
        "azp": "gen9-ui",
        "typ": "Bearer",
        "iat": now,
        "exp": now + 300,
        "email": "alan@gen9.test",
        "realm_access": {"roles": ["gen9-user", "offline_access"]},
        "sid": "session-1",
    } | overrides
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "k1"})


def test_valid_access_token(verifier, keypair):
    principal = verifier.verify(make_token(keypair[0]))
    assert principal.sub == "user-1"
    assert principal.client_id == "gen9-ui"
    assert principal.session_id == "session-1"
    assert principal.has_role("gen9-user") and not principal.has_role("gen9-admin")


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"exp": int(time.time()) - 60}, jwt.ExpiredSignatureError),
        ({"iss": "http://evil.example/realms/gen9"}, jwt.InvalidIssuerError),
        ({"aud": "account"}, jwt.InvalidAudienceError),
        ({"typ": "ID"}, jwt.InvalidTokenError),  # an ID token, not an access token
        ({"azp": "some-other-client"}, jwt.InvalidTokenError),
        ({"sub": None}, jwt.MissingRequiredClaimError),
    ],
    ids=[
        "expired",
        "wrong-issuer",
        "wrong-audience",
        "id-token",
        "unknown-client",
        "no-subject",
    ],
)
def test_rejected_tokens(verifier, keypair, overrides, error):
    with pytest.raises(error):
        verifier.verify(make_token(keypair[0], **overrides))


def test_rejects_token_signed_by_another_key(verifier):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(make_token(other))


def test_rejects_non_rs256_algorithms(verifier):
    """Algorithm confusion: only RS256 is accepted, whatever the token header says."""
    now = int(time.time())
    hmac_token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "gen9-agent",
            "sub": "x",
            "azp": "gen9-ui",
            "typ": "Bearer",
            "iat": now,
            "exp": now + 60,
        },
        "a-shared-secret-an-attacker-picked-32b",
        algorithm="HS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(jwt.InvalidAlgorithmError):
        verifier.verify(hmac_token)
