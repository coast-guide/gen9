"""Admin > Users search (keycloak_admin.contains): any part of a name or email matches, as the
page says; Keycloak's own search only matches the start. An admin's wildcard or exact query
passes as typed."""

import pytest

from gen9_agent.keycloak_admin import contains

pytestmark = pytest.mark.asyncio


async def test_a_plain_query_matches_anywhere() -> None:
    assert contains("gen9.test") == "*gen9.test*"
    assert contains("uinn") == "*uinn*"


async def test_an_admins_own_wildcard_or_exact_query_is_kept() -> None:
    assert contains("quinn*") == "quinn*"
    assert contains("*.com") == "*.com"
    assert contains('"quinn-1@gen9.test"') == '"quinn-1@gen9.test"'
    # A lone quote isn't an exact query
    assert contains('"') == '*"*'
