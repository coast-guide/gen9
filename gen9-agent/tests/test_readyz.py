"""Ready only when the database answers and has this build's schema, not merely answers."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from gen9_agent.api.health import readyz, schema_head, schema_revisions

pytestmark = pytest.mark.asyncio


class FakeDatabase:
    """Answers readyz's queries: whether each table exists, and the Alembic revision."""

    def __init__(
        self, version: str | None, checkpoints: bool = True, down: bool = False
    ):
        self.version, self.checkpoints, self.down = version, checkpoints, down

    async def scalar(self, statement):
        if self.down:
            raise ConnectionRefusedError("database down")
        sql = str(statement)
        if "to_regclass('public.alembic_version')" in sql:
            return self.version is not None
        if "from public.alembic_version" in sql:
            return self.version
        if "checkpoint_migrations" in sql:
            return self.checkpoints
        raise AssertionError(f"unexpected query: {sql}")


async def request_expecting(head: str | None) -> SimpleNamespace:
    """What readyz reads from the request: the head and the revisions the app loaded at startup."""
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                schema_head=head, schema_revisions=await schema_revisions()
            )
        )
    )


async def test_ready_when_migrated():
    head = await schema_head()
    assert head is not None
    request = await request_expecting(head)
    assert await readyz(FakeDatabase(head), request) == {"status": "ready"}  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "version, checkpoints, down, detail",
    [
        (None, True, False, "not migrated"),  # recreated empty: no tables at all
        ("older", True, False, "not migrated"),  # an older revision this code knows
        ("f00dfacecafe", True, False, "from a newer Gen9"),  # a rollback (P2-G2)
        ("head", False, False, "not migrated"),  # the checkpointer's tables are missing
        (None, True, True, "unavailable"),
    ],
)
async def test_not_ready(version, checkpoints, down, detail):
    head = await schema_head()
    older = next(r for r in await schema_revisions() if r != head)
    known = {"head": head, "older": older}
    database = FakeDatabase(known.get(version, version), checkpoints, down)
    with pytest.raises(HTTPException) as caught:
        await readyz(database, await request_expecting(head))  # ty: ignore[invalid-argument-type]
    assert caught.value.status_code == 503
    assert detail in caught.value.detail
