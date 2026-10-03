"""Which connector apps' hosts get a certificate (apps_host.py): gen9-edge's on-demand TLS asks
before getting one, and only a connector that exists says yes (docs/plans/deploy.md, U5b-5)."""

import uuid

import pytest

from gen9_agent.api.apps_host import apps_host

pytestmark = pytest.mark.asyncio


class Session:
    """One connector; records each lookup, so a refusal without one shows."""

    def __init__(self, connector: uuid.UUID) -> None:
        self.connector = connector
        self.asked: list[object] = []

    async def scalar(self, statement: object) -> object:
        self.asked.append(statement)
        wanted = statement.compile().params["id_1"]  # ty: ignore[unresolved-attribute]
        return wanted if wanted == self.connector else None


async def ask(session: Session, domain: str) -> int:
    return (await apps_host(session, domain)).status_code  # ty: ignore[invalid-argument-type]


async def test_a_connectors_host_may_have_one() -> None:
    connector = uuid.uuid4()
    session = Session(connector)
    assert await ask(session, f"{connector.hex}.apps.gen9.example.com") == 200
    # As browsers and Caddy may send it: upper case
    assert await ask(session, f"{connector.hex.upper()}.APPS.gen9.example.com") == 200


async def test_a_made_up_id_may_not() -> None:
    session = Session(uuid.uuid4())
    assert await ask(session, f"{uuid.uuid4().hex}.apps.gen9.example.com") == 404


async def test_other_names_are_refused_without_a_lookup() -> None:
    connector = uuid.uuid4()
    session = Session(connector)
    for domain in (
        "gen9.example.com",
        "api.gen9.example.com",
        f"x{connector.hex}.apps.gen9.example.com",
        f"{connector}.apps.gen9.example.com",  # with its hyphens: not how gen9-ui names it
        f"{connector.hex}.gen9.example.com",
    ):
        assert await ask(session, domain) == 404
    assert session.asked == []
