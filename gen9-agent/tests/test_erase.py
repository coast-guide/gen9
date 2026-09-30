"""gen9-agent-erase (erase.py): what a restored backup brought back is deleted again through
Gen9's own workflows, accounts first, their chats with them, and each recorded (P4-E5)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from gen9_agent import erase

pytestmark = pytest.mark.asyncio

ALAN_CHAT = uuid.uuid4()
BACK_CHAT = uuid.uuid4()
ROWLESS_CHAT = uuid.uuid4()
FIRST_VISIT = datetime(2026, 9, 20, tzinfo=UTC)


class Keycloak:
    """Who Keycloak holds: one account restored with Gen9's data, one with Keycloak's alone."""

    def __init__(self) -> None:
        self.held = {"back-with-data", "back-in-keycloak-only"}

    async def user_exists(self, sub: str) -> bool:
        return sub in self.held

    async def get_user(self, sub: str) -> dict:
        return {"createdTimestamp": datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000}


async def test_what_came_back_is_deleted_again_through_the_workflows(
    monkeypatch,
) -> None:
    steps: list[tuple] = []

    async def load(engine, users, threads):
        # Gen9's rows: the first account with a chat of its own; Alan's chat
        return {"back-with-data": FIRST_VISIT}, [
            (BACK_CHAT, FIRST_VISIT, "back-with-data"),
            (ALAN_CHAT, FIRST_VISIT, "alan"),
        ]

    async def start_account(temporal, sub, since, keycloak):
        steps.append(("account", sub, since, keycloak))
        return SimpleNamespace(kind="account", target=sub)

    async def start_thread(temporal, thread_id, created_at, owner):
        assert created_at == (erase.EPOCH if thread_id == ROWLESS_CHAT else FIRST_VISIT)
        steps.append(("chat", thread_id, owner))
        return SimpleNamespace(kind="chat", target=thread_id)

    async def deleted(handle):
        steps.append(("deleted", handle.kind, handle.target))

    async def record(engine, action, target):
        steps.append(("audit", action, target))

    monkeypatch.setattr(erase, "_load", load)
    monkeypatch.setattr(erase, "start_account_deletion", start_account)
    monkeypatch.setattr(erase, "start_thread_deletion", start_thread)
    monkeypatch.setattr(erase, "_deleted", deleted)
    monkeypatch.setattr(erase, "_record", record)
    done = await erase.erase(
        None,  # ty: ignore[invalid-argument-type]
        None,  # ty: ignore[invalid-argument-type]
        Keycloak(),  # ty: ignore[invalid-argument-type]
        ["back-with-data", "back-in-keycloak-only", "gone-everywhere"],
        [BACK_CHAT, ALAN_CHAT, ROWLESS_CHAT, ALAN_CHAT],
    )
    assert done == {"accounts": 3, "chats": 2}
    assert steps == [
        # Since their first visit; Keycloak's account deleted too
        ("account", "back-with-data", FIRST_VISIT, True),
        ("deleted", "account", "back-with-data"),
        ("audit", "restore.account.delete", "back-with-data"),
        # Only Keycloak's: since its account was made there
        ("account", "back-in-keycloak-only", datetime(2026, 9, 1, tzinfo=UTC), True),
        ("deleted", "account", "back-in-keycloak-only"),
        ("audit", "restore.account.delete", "back-in-keycloak-only"),
        # In neither any more: Gen9's other stores still are swept, Keycloak left alone
        ("account", "gone-everywhere", erase.EPOCH, False),
        ("deleted", "account", "gone-everywhere"),
        ("audit", "restore.account.delete", "gone-everywhere"),
        # The first account's chat went with it; Alan's is deleted on its own
        ("chat", ALAN_CHAT, "alan"),
        ("deleted", "chat", ALAN_CHAT),
        ("audit", "restore.thread.delete", str(ALAN_CHAT)),
        # No row (gen9-postgres wasn't restored): its workflow still erases what came back
        ("chat", ROWLESS_CHAT, erase.OWNER_UNKNOWN),
        ("deleted", "chat", ROWLESS_CHAT),
        ("audit", "restore.thread.delete", str(ROWLESS_CHAT)),
    ]


async def test_nothing_to_delete_again_touches_nothing(capsys) -> None:
    assert await erase._main([]) == 0
    assert "Nothing to delete again" in capsys.readouterr().out


class Result:
    """As SQLAlchemy's: rows to iterate, and keys(), which made dict() take it for a mapping and
    fail ("'CursorResult' object is not subscriptable", found in make restore)."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def keys(self) -> list[str]:
        return ["sub", "created_at"]


class Engine:
    def __init__(self, *results: list[tuple]) -> None:
        self.results = list(results)

    def connect(self):
        engine = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                pass

            async def execute(self, statement) -> Result:
                return Result(engine.results.pop(0))

        return Conn()


async def test_the_accounts_and_chats_are_read_from_their_rows() -> None:
    engine = Engine(
        [("back-with-data", FIRST_VISIT)],
        [(ALAN_CHAT, FIRST_VISIT, "alan")],
    )
    created, chats = await erase._load(engine, ["back-with-data"], [ALAN_CHAT])  # ty: ignore[invalid-argument-type]
    assert created == {"back-with-data": FIRST_VISIT}
    assert chats == [(ALAN_CHAT, FIRST_VISIT, "alan")]
