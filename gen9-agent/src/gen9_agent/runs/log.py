"""The run event log in Postgres (`run_events`), and how readers learn that it grew.

Writers (workers) append events in order and `pg_notify('gen9_run_events', <run id>)` in the same
transaction. Readers (the API's streams) read everything after the last sequence number they sent,
then wait on `EventHub`, which holds one `LISTEN` connection per process and wakes the streams of
that run. A missed notification only delays a reader until its next timeout, never loses an event:
the table is the source of truth, so a client that reconnects with `Last-Event-ID` gets the rest.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any

import psycopg
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ..models import RunEvent as RunEventRow
from .events import RunEvent

log = logging.getLogger(__name__)

EVENTS_CHANNEL = "gen9_run_events"


async def next_seq(conn: AsyncConnection, run_id: uuid.UUID) -> int:
    last = await conn.scalar(
        select(func.max(RunEventRow.seq)).where(RunEventRow.run_id == run_id)
    )
    return (last or 0) + 1


async def append(
    conn: AsyncConnection, run_id: uuid.UUID, seq: int, events: list[RunEvent]
) -> int:
    """Append `events` from `seq` on and notify readers; returns the next sequence number.
    Runs in the caller's transaction, so events and the run's own changes commit together."""
    if not events:
        return seq
    await conn.execute(
        insert(RunEventRow),
        [
            {"run_id": run_id, "seq": seq + i, "type": e.type, "data": e.data}
            for i, e in enumerate(events)
        ],
    )
    await conn.execute(
        text("SELECT pg_notify(:channel, :run)"),
        {"channel": EVENTS_CHANNEL, "run": str(run_id)},
    )
    return seq + len(events)


async def read_after(
    engine: AsyncEngine, run_id: uuid.UUID, after: int, limit: int = 500
) -> list[tuple[int, str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(RunEventRow.seq, RunEventRow.type, RunEventRow.data)
            .where(RunEventRow.run_id == run_id, RunEventRow.seq > after)
            .order_by(RunEventRow.seq)
            .limit(limit)
        )
        return [(r.seq, r.type, r.data) for r in rows]


class EventHub:
    """One `LISTEN gen9_run_events` connection for this process; `wait(run_id)` returns when that
    run's log grows (callers bound it with `asyncio.timeout`). Reconnects on its own if the
    connection drops."""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._listen_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def wait(self, run_id: uuid.UUID) -> None:
        event = asyncio.Event()
        key = str(run_id)
        self._waiters[key].add(event)
        try:
            await event.wait()
        finally:
            self._waiters[key].discard(event)
            if not self._waiters[key]:
                del self._waiters[key]

    async def _listen_forever(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {EVENTS_CHANNEL}")
                    async for notify in conn.notifies():
                        for event in self._waiters.get(notify.payload, ()):
                            event.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning(
                    "run event listener lost its connection; retrying", exc_info=True
                )
                await asyncio.sleep(2)
