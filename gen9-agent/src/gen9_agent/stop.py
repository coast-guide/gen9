"""Stops every agent at once, for an operator who needs Gen9 to do nothing more now
(docs/plans/manual-e2e.md, P5-C10; OWASP's ASI10, rogue agents): every run not yet over (queued,
running or waiting) is cancelled, and every scheduled task's Schedule is paused, with a note
saying so. `--resume` unpauses the Schedules it paused and no others, so a task its person paused
stays paused. Each is an audit event. `make stop-agents` runs it and then stops the worker, so
nothing new runs until `make resume-agents`.

    gen9-agent-stop [--resume]
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import Client

from .db import create_engine
from .keycloak_admin import KeycloakAdmin
from .models import AuditEvent
from .runs import control
from .settings import get_settings
from .temporal import connect
from .workflows.names import task_schedule_id

# The note on the Schedules it pauses: what `--resume` looks for
NOTE = "stopped by the operator (gen9-agent-stop)"
TASKS = task_schedule_id("")


async def _record(engine: AsyncEngine, action: str, detail: dict[str, int]) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(AuditEvent).values(
                actor="gen9-agent-stop",
                action=action,
                outcome="success",
                where="make stop-agents",
                detail=detail,
            )
        )


async def _task_schedules(temporal: Client) -> list[str]:
    return [
        s.id async for s in await temporal.list_schedules() if s.id.startswith(TASKS)
    ]


async def stop(engine: AsyncEngine, temporal: Client) -> dict[str, int]:
    """Every active run cancelled, every running task Schedule paused: how many of each."""
    # Their ends recorded before the worker stops (make stop-agents stops it next)
    runs = await control.stop_runs_of(engine, temporal, None, wait_s=60)
    paused = 0
    for schedule in await _task_schedules(temporal):
        handle = temporal.get_schedule_handle(schedule)
        if (await handle.describe()).schedule.state.paused:
            continue  # its person paused it, or an earlier stop did
        await handle.pause(note=NOTE)
        paused += 1
    done = {"runs": runs, "schedules": paused}
    await _record(engine, "operator.stop", done)
    return done


async def resume(engine: AsyncEngine, temporal: Client) -> dict[str, int]:
    """The task Schedules a stop paused, unpaused: how many."""
    unpaused = 0
    for schedule in await _task_schedules(temporal):
        handle = temporal.get_schedule_handle(schedule)
        state = (await handle.describe()).schedule.state
        if state.paused and state.note == NOTE:
            await handle.unpause(
                note="resumed by the operator (gen9-agent-stop --resume)"
            )
            unpaused += 1
    done = {"schedules": unpaused}
    await _record(engine, "operator.resume", done)
    return done


async def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="gen9-agent-stop", description=__doc__)
    parser.add_argument(
        "--resume", action="store_true", help="unpause what a stop paused"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            keycloak = (
                KeycloakAdmin(settings, http)
                if settings.keycloak_admin_client_secret
                else None
            )
            temporal = await connect(settings, "gen9-agent-stop", keycloak)
            if args.resume:
                done = await resume(engine, temporal)
                print(f"resumed: {done['schedules']} scheduled tasks")
            else:
                done = await stop(engine, temporal)
                print(
                    f"stopped: {done['runs']} runs, {done['schedules']} scheduled tasks paused"
                )
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))
