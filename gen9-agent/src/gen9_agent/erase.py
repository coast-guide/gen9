"""Deletes again what a restored backup brought back: the accounts and chats deleted after the
backup was made, whose ids `scripts/restore.sh` reads from the audit record (the person's, an
admin's, the sweep's or an earlier restore's deletions since the backup's time).
Each goes through Gen9's own deletion workflow, as the person's or an admin's would
(docs/temporal.md), and is recorded as an audit event. The ICO's guidance on erasure asks that
backup data stay "beyond use"; a restore puts it back into use, so an erasure made since is made
again (docs/plans/manual-e2e.md, P4-E5, and its Decision Log).

    gen9-agent-erase [--users SUB ...] [--threads ID ...]
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import Client, WorkflowHandle

from .db import create_engine
from .deletions import start_account_deletion, start_thread_deletion
from .keycloak_admin import KeycloakAdmin
from .models import AuditEvent, Thread, User
from .settings import get_settings
from .temporal import connect

# How long each deletion is given to say its data is gone (its `deleted` Update); the late trace
# erasures go on after
WAIT_S = 120
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Whose a chat was, when its row is gone: only the workflow's search attribute and fairness key
OWNER_UNKNOWN = "restore"


async def _deleted(handle: WorkflowHandle) -> None:
    await handle.execute_update("deleted", rpc_timeout=timedelta(seconds=WAIT_S))


async def _record(engine: AsyncEngine, action: str, target: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(AuditEvent).values(
                actor="gen9-agent-erase",
                action=action,
                outcome="success",
                target=target,
                where="make restore",
                detail={},
            )
        )


async def _load(
    engine: AsyncEngine, users: Sequence[str], threads: Sequence[uuid.UUID]
) -> tuple[dict[str, datetime], list[tuple[uuid.UUID, datetime, str]]]:
    """When each account first came to Gen9, and each chat's creation and owner."""
    async with engine.connect() as conn:
        # Rows unpacked one by one: dict() takes a result for a mapping, having keys()
        created = (
            {
                sub: at
                for sub, at in await conn.execute(
                    select(User.sub, User.created_at).where(User.sub.in_(users))
                )
            }
            if users
            else {}
        )
        chats = (
            [
                (thread_id, at, owner)
                for thread_id, at, owner in await conn.execute(
                    select(Thread.id, Thread.created_at, User.sub)
                    .join(User, User.id == Thread.user_id)
                    .where(Thread.id.in_(threads))
                )
            ]
            if threads
            else []
        )
    return created, chats


async def erase(
    engine: AsyncEngine,
    temporal: Client,
    keycloak: KeycloakAdmin | None,
    users: Sequence[str],
    threads: Sequence[uuid.UUID],
) -> dict[str, int]:
    """The accounts, then the chats not already gone with them. How many of each."""
    created, chats = await _load(engine, users, threads)
    done = {"accounts": 0, "chats": 0}
    for sub in users:
        in_keycloak = keycloak is not None and await keycloak.user_exists(sub)
        since = created.get(sub)
        if since is None and in_keycloak and keycloak is not None:
            since = datetime.fromtimestamp(
                (await keycloak.get_user(sub))["createdTimestamp"] / 1000, UTC
            )
        await _deleted(
            await start_account_deletion(
                temporal, sub, since or EPOCH, in_keycloak, again=True
            )
        )
        await _record(engine, "restore.account.delete", sub)
        print(f"account {sub}: deleted again", flush=True)
        done["accounts"] += 1
    rows = {thread_id: (created_at, owner) for thread_id, created_at, owner in chats}
    for thread_id in dict.fromkeys(threads):
        # One not in Gen9's database (gen9-postgres not restored) still has its workflow: its
        # traces or history may be back in the stores that were
        created_at, owner = rows.get(thread_id, (EPOCH, OWNER_UNKNOWN))
        if owner in users:
            continue  # its account's deletion took it
        await _deleted(
            await start_thread_deletion(
                temporal, thread_id, created_at, owner, again=True
            )
        )
        await _record(engine, "restore.thread.delete", str(thread_id))
        print(f"chat {thread_id}: deleted again", flush=True)
        done["chats"] += 1
    return done


async def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="gen9-agent-erase", description=__doc__)
    parser.add_argument("--users", nargs="*", default=[], metavar="SUB")
    parser.add_argument(
        "--threads", nargs="*", default=[], metavar="ID", type=uuid.UUID
    )
    args = parser.parse_args(argv)
    if not args.users and not args.threads:
        print("Nothing to delete again.")
        return 0
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            keycloak = (
                KeycloakAdmin(settings, http)
                if settings.keycloak_admin_client_secret
                else None
            )
            temporal = await connect(settings, "gen9-agent-erase", keycloak)
            done = await erase(engine, temporal, keycloak, args.users, args.threads)
    finally:
        await engine.dispose()
    print(f"deleted again: {done['accounts']} accounts, {done['chats']} chats")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))
