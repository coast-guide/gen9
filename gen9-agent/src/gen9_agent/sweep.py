"""The people Gen9 knows whom Keycloak no longer has, as the deleted-users sweep finds them, for an
admin whose sweep held (docs/plans/deploy.md, U5c-6). The sweep deletes nobody when more are
missing at once than SWEEP_MAX_DELETIONS, or more than half of the people Gen9 knows: that looks
like a Keycloak that changed (another realm, an empty database), not people deleted one by one.

    gen9-agent-sweep              how many are missing, and whether the sweep holds; changes nothing
    gen9-agent-sweep --allow N    the sweep run once now, deleting them if they are N or fewer

`--allow` is the admin's word that they were deleted on purpose: each is then deleted as any
deleted account is, and recorded. Run it in the worker (`docker exec gen9-agent-worker-1 …`;
`kubectl exec deploy/worker -c worker -- …`).
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

import httpx
from sqlalchemy import func, insert, select
from temporalio.common import SearchAttributePair, TypedSearchAttributes

from . import accounts
from .db import create_engine, create_sessionmaker
from .keycloak_admin import KeycloakAdmin
from .models import AuditEvent, User
from .settings import get_settings
from .temporal import GEN9_KIND, connect
from .workflows.deletion import SweepDeletedUsersWorkflow
from .workflows.names import SYSTEM_QUEUE


async def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="gen9-agent-sweep", description=__doc__)
    parser.add_argument(
        "--allow",
        type=int,
        metavar="N",
        help="run the sweep now, deleting the missing people if they are N or fewer",
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
                missing = await accounts.find_deleted_users(session, keycloak)
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


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))
