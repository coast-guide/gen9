"""gen9-agent-sweep names whom a held sweep would delete, so an admin can judge before deleting,
and `--only` deletes just the people named, each one Keycloak no longer has (P8-B4)."""

from datetime import UTC, datetime

import pytest

from gen9_agent.sweep import Missing, chosen, describe

pytestmark = pytest.mark.asyncio


def person(
    sub: str, day: int, email: str | None = "x@gen9.test", chats: int = 0
) -> Missing:
    when = datetime(2026, 10, day, tzinfo=UTC)
    return Missing(sub, when, email, "X" if email else None, when, chats)


async def test_each_missing_person_is_named_oldest_visit_first() -> None:
    lines = describe([person("b", 3, chats=1), person("a", 1, email=None, chats=2)])
    assert lines == [
        "  a  (no email)  (no name)  last seen 2026-10-01  2 chats",
        "  b  x@gen9.test  X  last seen 2026-10-03  1 chat",
    ]


async def test_only_the_named_missing_people_are_chosen() -> None:
    people = [person("a", 1), person("b", 2), person("c", 3)]
    picked, refused = chosen(people, ["c", "a", "c"])
    assert [p.sub for p in picked] == ["c", "a"]
    assert refused == []


async def test_a_name_keycloak_still_has_refuses_them_all() -> None:
    _, refused = chosen([person("a", 1)], ["a", "still-in-keycloak"])
    assert refused == ["still-in-keycloak"]
