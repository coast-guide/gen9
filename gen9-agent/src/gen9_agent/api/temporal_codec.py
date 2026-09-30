"""The codec endpoint Temporal's web UI calls to show payloads, which gen9-agent encrypts
(codec.py): `POST /v1/temporal/codec/decode` and `/encode`, per Temporal's Codec Server protocol
(docs.temporal.io/production-deployment/data-encryption#codec-server-setup). Adapted from
Temporal's Python sample (samples-python/encryption/codec_server.py).

Only Gen9 admins signed in to the UI may use it: the UI passes their Keycloak access token
(TEMPORAL_CODEC_PASS_ACCESS_TOKEN), which must come from client temporal-ui and grant gen9:admin.
It is its own app, mounted by app.py, so CORS for the UI's origin covers these two paths only.
"""

from collections.abc import Awaitable, Callable, Iterable

import jwt
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from google.protobuf import json_format
from temporalio.api.common.v1 import Payload, Payloads

from ..auth import TokenVerifier
from ..codec import EncryptionCodec

ADMIN_PERMISSION = "gen9:admin"

codec_app = FastAPI(
    title="Temporal codec", openapi_url=None, docs_url=None, redoc_url=None
)


async def _admin(request: Request) -> None:
    verifier: TokenVerifier = codec_app.state.verifier
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sign in to Temporal's web UI"
        )
    try:
        # PyJWT has no async key client: the verifier may fetch Keycloak's keys, in a thread
        principal = await run_in_threadpool(verifier.verify, token)
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
    if ADMIN_PERMISSION not in principal.claims.get("permissions", []):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Gen9 admins see payloads")


async def _apply(
    request: Request, fn: Callable[[Iterable[Payload]], Awaitable[list[Payload]]]
) -> Response:
    await _admin(request)
    try:
        payloads = json_format.Parse(await request.body(), Payloads())
    except json_format.ParseError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Expected {payloads: [...]}"
        ) from None
    result = Payloads(payloads=await fn(payloads.payloads))
    return Response(json_format.MessageToJson(result), media_type="application/json")


@codec_app.post("/decode")
async def decode(request: Request) -> Response:
    codec: EncryptionCodec = codec_app.state.codec
    return await _apply(request, codec.decode)


@codec_app.post("/encode")
async def encode(request: Request) -> Response:
    codec: EncryptionCodec = codec_app.state.codec
    return await _apply(request, codec.encode)
