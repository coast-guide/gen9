"""Sensitive actions need a recent sign-in (RFC 9470 step-up challenge otherwise)."""

import time

import pytest
from fastapi import HTTPException

from gen9_agent.auth import Principal, require_recent_authentication

pytestmark = pytest.mark.asyncio

check = require_recent_authentication(max_age=300)


def principal(**claims) -> Principal:
    return Principal(sub="user-1", client_id="gen9-ui", claims=claims)


async def test_recent_sign_in_passes():
    caller = principal(auth_time=int(time.time()) - 60)
    assert await check(caller) is caller


@pytest.mark.parametrize(
    "claims",
    [{"auth_time": int(time.time()) - 301}, {}, {"auth_time": "yesterday"}],
    ids=["too-old", "missing", "not-a-number"],
)
async def test_stale_or_missing_sign_in_gets_step_up_challenge(claims):
    with pytest.raises(HTTPException) as caught:
        await check(principal(**claims))
    assert caught.value.status_code == 401
    challenge = caught.value.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'max_age="300"' in challenge
