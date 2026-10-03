"""Users deleted in Keycloak directly: only users confirmed missing by their own lookup are swept,
so an incomplete listing never deletes anyone."""

import json
import logging
from datetime import UTC, datetime

import pytest

from gen9_agent.accounts import MissingUser, find_deleted_users

pytestmark = pytest.mark.asyncio

CREATED = datetime(2026, 9, 1, tzinfo=UTC)


class Rows:
    def __init__(self, subs):
        self.subs = subs

    def all(self):
        return [(sub, CREATED) for sub in self.subs]


class FakeSession:
    def __init__(self, subs):
        self.subs = subs

    async def execute(self, statement):
        return Rows(self.subs)


class FakeKeycloak:
    def __init__(self, listed, exist_individually=()):
        self.listed = set(listed)
        self.exist_individually = set(exist_individually)
        self.lookups: list[str] = []

    async def user_ids(self):
        return self.listed

    async def user_exists(self, sub):
        self.lookups.append(sub)
        return sub in self.exist_individually


async def test_only_confirmed_missing_users_are_found():
    # kept: listed; kept: missing from the listing but found by its own lookup; found: 404
    keycloak = FakeKeycloak(listed={"kept"}, exist_individually={"unlisted-but-exists"})
    missing = await find_deleted_users(
        FakeSession(["kept", "unlisted-but-exists", "deleted"]),  # ty: ignore[invalid-argument-type]
        keycloak,  # ty: ignore[invalid-argument-type]
    )
    assert missing == [MissingUser("deleted", CREATED)]
    assert keycloak.lookups == ["unlisted-but-exists", "deleted"]


async def test_empty_listing_finds_nobody_without_confirmation():
    # A broken listing (nothing returned) must not sweep everyone: each lookup still finds them
    keycloak = FakeKeycloak(listed=set(), exist_individually={"a", "b"})
    assert await find_deleted_users(FakeSession(["a", "b"]), keycloak) == []  # ty: ignore[invalid-argument-type]


async def test_each_swept_account_is_recorded_once(monkeypatch, caplog):
    """Each deletion the sweep starts is in the audit record, as the person's or an admin's: the
    record `make restore` reads to delete it again (P4-E5), and a line of the worker's log, as
    every audit record is a line of the log of the process that makes it (docs/logging.md). A
    retried attempt adds none twice."""
    from types import SimpleNamespace

    from gen9_agent import deletion

    added: list = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def scalars(self, statement):
            return [a.target for a in added]

        def add(self, row):
            added.append(row)

        async def commit(self):
            pass

    async def missing(session, keycloak):
        return [MissingUser("gone-1", CREATED), MissingUser("gone-2", CREATED)]

    monkeypatch.setattr(deletion.accounts, "find_deleted_users", missing)
    runtime = SimpleNamespace(sessionmaker=Session)
    activities = deletion.DeletionActivities(runtime, None, object())  # ty: ignore[invalid-argument-type]
    caplog.set_level(logging.INFO)
    first = await activities.find_deleted_users()
    await activities.find_deleted_users()  # retried
    assert [u.sub for u in first] == ["gone-1", "gone-2"]
    assert [(a.actor, a.action, a.target) for a in added] == [
        ("sweep", "account.sweep", "gone-1"),
        ("sweep", "account.sweep", "gone-2"),
    ]
    lines = [
        json.loads(r.getMessage().removeprefix("audit "))
        for r in caplog.records
        if r.getMessage().startswith("audit {")
    ]
    assert [(e["actor"], e["action"], e["target"], e["where"]) for e in lines] == [
        ("sweep", "account.sweep", "gone-1", "SweepDeletedUsersWorkflow"),
        ("sweep", "account.sweep", "gone-2", "SweepDeletedUsersWorkflow"),
    ]
