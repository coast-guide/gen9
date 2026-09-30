from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from gen9_agent.api import export
from gen9_agent.api.export import file_name


def test_a_file_keeps_its_name_in_its_chats_folder() -> None:
    taken: set[str] = set()
    assert file_name("files/t1", "report.csv", taken) == "files/t1/report.csv"
    assert file_name("files/t2", "report.csv", taken) == "files/t2/report.csv"


def test_two_of_one_name_are_numbered() -> None:
    taken: set[str] = set()
    names = [
        file_name("files/t", n, taken)
        for n in ("a.txt", "a.txt", "a.txt", "notes", "notes")
    ]
    assert names == [
        "files/t/a.txt",
        "files/t/a (2).txt",
        "files/t/a (3).txt",
        "files/t/notes",
        "files/t/notes (2)",
    ]


def test_a_name_can_never_leave_its_folder() -> None:
    taken: set[str] = set()
    for name in ("../../etc/passwd", "..\\..\\x.txt", "/abs/y.txt", ".."):
        path = file_name("files/t", name, taken)
        assert path.startswith("files/t/") and ".." not in path.removeprefix(
            "files/t/"
        ).split("/")
    assert file_name("files/t", "", taken).startswith("files/t/file")


# What else Gen9 keeps about the person (GDPR Art. 15; manual-e2e.md, P5-B5)


class Rows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class AuditSession:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def scalars(self, statement):
        return Rows(self.rows)


@pytest.mark.asyncio
async def test_the_audit_names_only_the_person_and_roles() -> None:
    at = datetime(2026, 9, 28, tzinfo=UTC)

    def event(actor, action, target=None):
        return SimpleNamespace(
            at=at,
            actor=actor,
            action=action,
            outcome="success",
            target=target,
            where="POST /v1/x",
            detail={},
        )

    rows = [
        event("me", "connector.add", "c1"),
        event("admin-sub", "admin.user.update", "me"),
        event("sweep", "account.sweep", "me"),
    ]
    entries = await export._audit(AuditSession(rows), "me")  # ty: ignore[invalid-argument-type]
    assert [(e["by"], e["action"], e["target"]) for e in entries] == [
        ("you", "connector.add", "c1"),
        ("an administrator", "admin.user.update", "you"),
        ("Gen9", "account.sweep", "you"),
    ]
    assert "admin-sub" not in str(entries)


@pytest.mark.asyncio
async def test_usage_that_cant_be_read_says_so_rather_than_failing_the_export(
    monkeypatch,
) -> None:
    class Down:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            pass

        async def get(self, url, headers):
            raise httpx.ConnectError("the router's admin API is down")

    monkeypatch.setattr(export.httpx, "AsyncClient", Down)
    settings = SimpleNamespace(
        gen9_models_admin_url="http://admin", gen9_models_key=SecretStr("k")
    )
    assert await export._usage(settings, "me") == {"unavailable": export.UNAVAILABLE}  # ty: ignore[invalid-argument-type]


class Keycloak:
    """The sign-in service's records of one person, as gen9-agent's admin client reads them."""

    def __init__(self, down: bool = False) -> None:
        self.down = down

    async def consents(self, user_id: str) -> list[dict]:
        if self.down:
            raise export.KeycloakAdminError(502, "Keycloak returned 503")
        return [
            {
                "clientId": "gen9-cli",
                "grantedClientScopes": ["roles", "email"],
                "createdDate": 1790700000000,
                "lastUpdatedDate": 1790700000000,
            }
        ]

    async def events(self, user_id: str) -> list[dict]:
        if self.down:
            raise export.KeycloakAdminError(502, "Keycloak returned 503")
        return [
            {
                "time": 1790700000000,
                "type": "LOGIN",
                "clientId": "gen9-ui",
                "ipAddress": "10.0.0.7",
                "sessionId": "s1",
                "details": {"code_id": "c"},
            },
            {
                "time": 1790690000000,
                "type": "LOGIN_ERROR",
                "clientId": "gen9-ui",
                "ipAddress": "10.0.0.7",
                "error": "invalid_user_credentials",
            },
        ]


def _request(keycloak: object) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(keycloak_admin=keycloak))
    )


@pytest.mark.asyncio
async def test_the_apps_a_person_allowed_and_their_sign_ins_are_in_the_export() -> None:
    # What the sign-in service keeps about them (gen9-learn.md, M9, F14)
    person = SimpleNamespace(sub="sub-a")
    apps = await export._apps(person, _request(Keycloak()))  # ty: ignore[invalid-argument-type]
    assert apps == [
        {
            "app": "gen9-cli",
            "scopes": ["email", "roles"],
            "allowed_at": "2026-09-29T16:40:00+00:00",
            "changed_at": "2026-09-29T16:40:00+00:00",
        }
    ]
    sign_ins = await export._sign_ins(person, _request(Keycloak()))  # ty: ignore[invalid-argument-type]
    assert sign_ins == [
        {
            "at": "2026-09-29T16:40:00+00:00",
            "type": "LOGIN",
            "app": "gen9-ui",
            "ip": "10.0.0.7",
        },
        {
            "at": "2026-09-29T13:53:20+00:00",
            "type": "LOGIN_ERROR",
            "app": "gen9-ui",
            "ip": "10.0.0.7",
            "error": "invalid_user_credentials",
        },
    ]


@pytest.mark.asyncio
async def test_the_sign_in_services_parts_say_so_when_it_cant_be_read() -> None:
    person = SimpleNamespace(sub="sub-a")
    for keycloak in (None, Keycloak(down=True)):
        assert await export._apps(person, _request(keycloak)) == {
            "unavailable": export.UNAVAILABLE
        }  # ty: ignore[invalid-argument-type]
        assert await export._sign_ins(person, _request(keycloak)) == {
            "unavailable": export.UNAVAILABLE
        }  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_sign_in_records_are_read_a_hundred_at_a_time() -> None:
    from gen9_agent.keycloak_admin import KeycloakAdmin

    asked: list[dict] = []

    class Paged(KeycloakAdmin):
        def __init__(self) -> None:
            pass

        async def _request(self, method, path, **kwargs):
            asked.append(kwargs["params"])
            size = 100 if kwargs["params"]["first"] < 200 else 7
            return SimpleNamespace(json=lambda: [{"n": i} for i in range(size)])

    events = await Paged().events("sub-a")
    assert len(events) == 207 and [a["first"] for a in asked] == [0, 100, 200]
    assert all(a["user"] == "sub-a" and a["max"] == 100 for a in asked)
