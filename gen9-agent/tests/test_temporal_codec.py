"""The codec endpoint for Temporal's web UI decodes payloads for Gen9 admins only: an access token
from client temporal-ui, issued for Temporal, granting gen9:admin. Tokens are signed here and
checked by the real TokenVerifier."""

import time

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from google.protobuf import json_format
from temporalio.api.common.v1 import Payloads
from temporalio.converter import default
from test_auth import ISSUER, StaticKeys

from gen9_agent.api.temporal_codec import codec_app
from gen9_agent.auth import TokenVerifier
from gen9_agent.codec import EncryptionCodec, new_key, parse_keys

pytestmark = pytest.mark.asyncio

PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CODEC = EncryptionCodec(parse_keys(new_key("k1")))


def token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": ["temporal", "account"],
        "azp": "temporal-ui",
        "typ": "Bearer",
        "sub": "admin-sub",
        "iat": now,
        "exp": now + 300,
        "permissions": ["temporal-system:read", "gen9:admin"],
    } | overrides
    return jwt.encode(claims, PRIVATE, algorithm="RS256", headers={"kid": "k1"})


@pytest_asyncio.fixture
async def client():
    codec_app.state.verifier = TokenVerifier(
        issuer=ISSUER,
        audience="temporal",
        allowed_clients=frozenset({"temporal-ui"}),
        keys=StaticKeys(PRIVATE.public_key()),
    )
    codec_app.state.codec = CODEC
    transport = httpx.ASGITransport(app=codec_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://codec") as c:
        yield c


async def encrypted_body(value) -> str:
    plain = default().payload_converter.to_payloads([value])
    return json_format.MessageToJson(Payloads(payloads=await CODEC.encode(plain)))


async def decode(client, bearer: str | None, body: str = '{"payloads": []}'):
    headers = {"content-type": "application/json", "x-namespace": "gen9"}
    if bearer is not None:
        headers["authorization"] = f"Bearer {bearer}"
    return await client.post("/decode", content=body, headers=headers)


async def test_admin_reads_decrypted_payloads(client):
    response = await decode(
        client, token(), await encrypted_body({"user_sub": "sub-1"})
    )
    assert response.status_code == 200
    [payload] = json_format.Parse(response.text, Payloads()).payloads
    assert payload.metadata["encoding"] == b"json/plain"
    assert default().payload_converter.from_payloads([payload]) == [
        {"user_sub": "sub-1"}
    ]


async def test_encode_round_trips(client):
    plain = json_format.MessageToJson(
        Payloads(payloads=default().payload_converter.to_payloads(["hi"]))
    )
    response = await client.post(
        "/encode", content=plain, headers={"authorization": f"Bearer {token()}"}
    )
    [payload] = json_format.Parse(response.text, Payloads()).payloads
    assert payload.metadata["encoding"] == b"binary/encrypted"


@pytest.mark.parametrize(
    "bearer",
    [
        pytest.param(None, id="no token"),
        pytest.param(token(azp="gen9-agent"), id="another client"),
        pytest.param(token(aud="account"), id="not issued for Temporal"),
        pytest.param(token(exp=int(time.time()) - 60), id="expired"),
        pytest.param(token(typ="ID"), id="an ID token"),
    ],
)
async def test_refuses_tokens_not_for_it(client, bearer):
    assert (await decode(client, bearer)).status_code == 401


@pytest.mark.parametrize(
    "permissions",
    [[], ["gen9:write"], ["temporal-system:read"], ["other:admin"]],
    ids=["none", "gen9-write", "system-read", "other-namespace-admin"],
)
async def test_only_gen9_admins(client, permissions):
    response = await decode(client, token(permissions=permissions))
    assert response.status_code == 403


async def test_rejects_a_body_that_is_not_payloads(client):
    assert (await decode(client, token(), "not json")).status_code == 400
