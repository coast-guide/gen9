"""The people Gen9 knows whom Keycloak no longer has, as the deleted-users sweep finds them, for an
admin whose sweep held (docs/plans/deploy.md, U5c-6). The sweep deletes nobody when more are
missing at once than SWEEP_MAX_DELETIONS, or more than half of the people Gen9 knows: that looks
like a Keycloak that changed (another realm, an empty database), not people deleted one by one.

    gen9-agent-sweep                 who is missing (each by id, email, name, last visit and
                                     chats), and whether the sweep holds; changes nothing
    gen9-agent-sweep --allow N       the sweep run once now, deleting them if they are N or fewer
    gen9-agent-sweep --only ID ...   these people deleted, each confirmed missing from Keycloak;
                                     the others stay (nothing is deleted if one isn't missing)

`--allow` and `--only` are the admin's word that they were deleted on purpose: each is then
deleted as any deleted account is, and recorded. Run it in the worker (`docker exec
gen9-agent-worker-1 …`; `kubectl exec deploy/worker -c worker -- …`).
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import func, insert, select
from temporalio.common import SearchAttributePair, TypedSearchAttributes

from . import accounts
from .db import create_engine, create_sessionmaker
from .deletions import start_account_deletion
from .keycloak_admin import KeycloakAdmin
from .models import AuditEvent, Thread, User
from .settings import get_settings
from .temporal import GEN9_KIND, connect
from .workflows.deletion import SweepDeletedUsersWorkflow
from .workflows.names import SYSTEM_QUEUE


@dataclass(frozen=True)
class Missing:
    """A person Keycloak no longer has, as an admin needs to recognize them."""

    sub: str
    since: datetime
    email: str | None
    name: str | None
    last_seen: datetime
    chats: int


def describe(people: Sequence[Missing]) -> list[str]:
    """One line each, oldest visit first: the id `--only` takes, then who they were."""
    return [
        f"  {p.sub}  {p.email or '(no email)'}  {p.name or '(no name)'}  "
        f"last seen {p.last_seen:%Y-%m-%d}  {p.chats} chat{'' if p.chats == 1 else 's'}"
        for p in sorted(people, key=lambda p: p.last_seen)
    ]


def chosen(
    people: Sequence[Missing], only: Sequence[str]
) -> tuple[list[Missing], list[str]]:
    """The named people who are missing, and the names that aren't (still in Keycloak, or unknown
    to Gen9): any of the latter, and nothing is deleted."""
    by_sub = {p.sub: p for p in people}
    named = list(dict.fromkeys(only))
    return [by_sub[s] for s in named if s in by_sub], [
        s for s in named if s not in by_sub
    ]


async def _missing(session, keycloak: KeycloakAdmin) -> list[Missing]:
    found = await accounts.find_deleted_users(session, keycloak)
    if not found:
        return []
    chats = (
        select(func.count())
        .select_from(Thread)
        .where(Thread.user_id == User.id)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(User.sub, User.email, User.name, User.last_seen_at, chats).where(
            User.sub.in_([m.sub for m in found])
        )
    )
    details = {sub: (email, name, seen, n) for sub, email, name, seen, n in rows}
    return [Missing(m.sub, m.since, *details[m.sub]) for m in found]


async def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="gen9-agent-sweep",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument(
        "--allow",
        type=int,
        metavar="N",
        help="run the sweep now, deleting the missing people if they are N or fewer",
    )
    which.add_argument(
        "--only",
        nargs="+",
        metavar="ID",
        help="delete these missing people (their ids, as listed), and no one else",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.keycloak_admin_client_secret:
        print(
            "KEYCLOAK_ADMIN_CLIENT_SECRET is not set: no sweep without it",
            file=sys.stderr,
        )
        return 2
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            keycloak = KeycloakAdmin(settings, http)
            async with create_sessionmaker(engine)() as session:
                missing = await _missing(session, keycloak)
                known = (
                    await session.scalar(select(func.count()).select_from(User)) or 0
                )
            held = accounts.sweep_holds(
                len(missing), known, settings.sweep_max_deletions
            )
            if held:
                print(f"the sweep holds: {held}")
            elif missing:
                print(
                    f"{len(missing)} of the {known} people Gen9 knows are missing from Keycloak: the sweep deletes them"
                )
            else:
                print(
                    f"Keycloak has each of the {known} people Gen9 knows: the sweep deletes nobody"
                )
            print("\n".join(describe(missing)))
            if args.only is not None:
                return await _only(settings, keycloak, engine, missing, args.only)
            if args.allow is None:
                return 0
            temporal = await connect(settings, "gen9-agent-sweep", keycloak)
            started = await temporal.execute_workflow(
                SweepDeletedUsersWorkflow.run,
                args.allow,
                id=f"sweep-deleted-users-allowed-{uuid.uuid4()}",
                task_queue=SYSTEM_QUEUE,
                search_attributes=TypedSearchAttributes(
                    [SearchAttributePair(GEN9_KIND, "sweep")]
                ),
            )
        async with engine.begin() as conn:
            await conn.execute(
                insert(AuditEvent).values(
                    actor="gen9-agent-sweep",
                    action="account.sweep.allowed",
                    outcome="success",
                    where="gen9-agent-sweep --allow",
                    detail={
                        "allow": args.allow,
                        "missing": len(missing),
                        "started": started,
                    },
                )
            )
        print(f"the sweep ran with --allow {args.allow}: {started} deletions started")
    finally:
        await engine.dispose()
    return 0


async def _only(settings, keycloak, engine, missing, only) -> int:
    people, refused = chosen(missing, only)
    if refused:
        print(
            f"nothing deleted: Keycloak still has, or Gen9 doesn't know, {', '.join(refused)}",
            file=sys.stderr,
        )
        return 1
    temporal = await connect(settings, "gen9-agent-sweep", keycloak)
    # Each one recorded as the sweep records it, the record `make restore` reads to delete them
    # again should a backup bring them back (deletion.py), then deleted as any deleted account is
    async with engine.begin() as conn:
        recorded = set(
            await conn.scalars(
                select(AuditEvent.target).where(
                    AuditEvent.action == "account.sweep",
                    AuditEvent.target.in_([p.sub for p in people]),
                )
            )
        )
        for p in people:
            if p.sub not in recorded:
                await conn.execute(
                    insert(AuditEvent).values(
                        actor="gen9-agent-sweep",
                        action="account.sweep",
                        outcome="success",
                        target=p.sub,
                        where="gen9-agent-sweep --only",
                        detail={},
                    )
                )
    for p in people:
        await start_account_deletion(temporal, p.sub, p.since, keycloak=False)
    async with engine.begin() as conn:
        await conn.execute(
            insert(AuditEvent).values(
                actor="gen9-agent-sweep",
                action="account.sweep.allowed",
                outcome="success",
                where="gen9-agent-sweep --only",
                detail={"only": len(people), "missing": len(missing)},
            )
        )
    print(f"{len(people)} deletion{'' if len(people) == 1 else 's'} started")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))
