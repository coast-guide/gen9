"""Run rows in Postgres: what people see of a run (status, input, error) and its first and last
events. Temporal executes runs (`workflows/runs.py`, `runs/activities.py`); this module records
them.

A thread has at most one active (queued or running) run: a unique partial index enforces it, so a
second message while one is running is refused rather than interleaved. Everything here is safe to
repeat, because Temporal runs an Activity at least once.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ..models import FINAL_RUN_STATUSES, InputRequest, Run, Thread, User
from ..workflows import names
from . import log
from .events import RunEvent

# What a person sees when a run fails; the row's `error` keeps the detail
PUBLIC_ERROR = "The agent failed to answer. Try again."
# The executor's error type when the model router refuses the user as over budget
# (model_router.budget_exceeded), and what the person sees then
BUDGET_EXCEEDED = names.BUDGET_EXCEEDED
PUBLIC_BUDGET_ERROR = "You've reached your model usage limit for now. Try again once it resets, or ask an admin."
# ...and over their requests per minute (model_router.over_rate_limit)
RATE_LIMITED = names.RATE_LIMITED
PUBLIC_RATE_LIMITED = (
    "You've sent more requests this minute than your limit allows. Retry in a minute."
)
# The turn didn't fit the context budget even after summarizing (Deep Agents' ContextOverflowError)
PUBLIC_TOO_LONG = (
    "This didn't fit in what the model can read at once, even after summarizing the chat. "
    "Start a new chat."
)


def _over_budget(error: str) -> str:
    """The budget refusal, with when it resets if the error says (runs/executor.py)."""
    found = re.search(r"\(resets (\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", error)
    if not found:
        return PUBLIC_BUDGET_ERROR
    when = datetime.fromisoformat(found[1])
    return (
        "You've reached your model usage limit. It resets on "
        f"{when.day} {when:%B %Y} at {when:%H:%M} UTC: try again then, or ask an admin."
    )


def public_error(error: str | None) -> str:
    """What a person sees for a failed run, from the detail kept in its row. The workflow keeps the
    innermost cause (workflows/runs.py, `_describe`): for a budget refusal, the router's own error,
    whose type is `budget_exceeded`."""
    if error and ("budget_exceeded" in error or f"{BUDGET_EXCEEDED}: " in error):
        return _over_budget(error)
    if error and f"{RATE_LIMITED}: " in error:
        return PUBLIC_RATE_LIMITED
    if error and "ContextOverflowError" in error:
        return PUBLIC_TOO_LONG
    return PUBLIC_ERROR


# Why a run waits for Retry, in plain words (`park`): the router passes the provider's own message
_OUT_OF_CREDITS = (
    "no credits remaining",
    "insufficient_quota",
    "exceeded your current quota",
)
PUBLIC_NO_CREDITS = (
    "The model provider has no credits left. Ask an admin to add some, then retry."
)
PUBLIC_NO_ANSWER = "The model provider didn't answer. Retry in a moment."
# The database refusing writes or gone mid-turn (P6-B3): it read as the provider's fault. Names as
# the failure's detail carries them (Temporal's ApplicationError keeps the exception's type)
_DATABASE = (
    "ReadOnlySqlTransaction",
    "DiskFull",
    "InsufficientResources",
    "OutOfMemory",
    "AdminShutdown",
    "OperationalError",
    "No space left on device",
)
PUBLIC_NO_SAVE = "Gen9 couldn't save its work. Retry in a moment."


def retry_reason(error: str) -> str:
    """What a person sees on a run waiting for Retry, from the failure's detail."""
    if "budget_exceeded" in error or f"{BUDGET_EXCEEDED}: " in error:
        return _over_budget(error)
    if f"{RATE_LIMITED}: " in error or "exceeded for end_user" in error:
        return PUBLIC_RATE_LIMITED
    if any(marker in error for marker in _OUT_OF_CREDITS):
        return PUBLIC_NO_CREDITS
    if any(marker in error for marker in _DATABASE):
        return PUBLIC_NO_SAVE
    return PUBLIC_NO_ANSWER


class ActiveRunExists(Exception):
    """The thread already has a queued or running run."""


@dataclass(frozen=True)
class StartedRun:
    id: uuid.UUID
    thread_id: uuid.UUID
    input: dict[str, Any]
    user_sub: str
    started_at: datetime | None
    # A background task's chat: the chat that started it, whose environment it uses
    parent_id: uuid.UUID | None = None
    # The person lets the agent search their past chats (past_chats.py)
    search_past_chats: bool = True
    # The person lets Gen9 remember things about them (memory.py)
    remember: bool = True


# A chat's name, at most (as a rename allows, api/threads.py)
TITLE_MAX = 80


def clipped_title(text: str, default: str) -> str:
    """The first line of `text` as a chat's name, or `default` for a blank one. A longer line is
    cut at a word and ends in "…", within `TITLE_MAX`: cut mid-word, a name read as if that were
    all of it ("… One sentence, wit": manual-e2e.md, P8-I4)."""
    first = next(iter(text.strip().splitlines()), "").strip()
    if len(first) <= TITLE_MAX:
        return first or default
    cut = first[: TITLE_MAX - 1]
    space = cut.rfind(" ")
    if space >= TITLE_MAX // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def chat_title(message: str) -> str:
    """A chat's title from its first message (`clipped_title`). A blank one, which the API refuses
    now, once failed the end of every attempt (gen9-learn.md, M9, F8)."""
    return clipped_title(message, "New chat")


async def enqueue(
    engine: AsyncEngine, thread_id: uuid.UUID, input: dict[str, Any]
) -> uuid.UUID:
    """Record a new run for `thread_id`; its log starts with `run.queued`. The chat keeps the
    run's permission mode, if it names one (approvals.py), and a chat still called "New chat"
    takes its message's first line as its name now, not when the answer succeeds: a first
    answer waiting for Allow had left "New chat · Needs you" in the sidebar (M9, F21). A name
    already given (a rename, a task's, a background task's) stays."""
    try:
        async with engine.begin() as conn:
            run_id = await conn.scalar(
                insert(Run).values(thread_id=thread_id, input=input).returning(Run.id)
            )
            assert run_id is not None
            if mode := input.get("permission_mode"):
                await conn.execute(
                    update(Thread)
                    .where(Thread.id == thread_id)
                    .values(permission_mode=mode)
                )
            if message := input.get("message"):
                await conn.execute(
                    update(Thread)
                    .where(Thread.id == thread_id, Thread.title == "New chat")
                    .values(title=chat_title(message))
                )
            await log.append(
                conn, run_id, 1, [RunEvent("run.queued", {"run_id": str(run_id)})]
            )
            return run_id
    except IntegrityError as e:
        if "uq_runs_one_active_per_thread" in str(e.orig):
            raise ActiveRunExists from e
        raise


async def start(
    engine: AsyncEngine, run_id: uuid.UUID, attempt: int, agent_version: str
) -> tuple[StartedRun, int] | None:
    """Mark attempt `attempt` of the run as started by the agent at `agent_version`, and append
    `run.started`. Returns the run and the next event sequence number, or None if the run already
    ended (a repeated Activity)."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                select(Run.status).where(Run.id == run_id).with_for_update()
            )
        ).first()
        if row is None or row.status in FINAL_RUN_STATUSES:
            return None
        run = (
            await conn.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status="running",
                    attempts=attempt,
                    agent_version=agent_version,
                    started_at=text("coalesce(started_at, now())"),
                )
                .returning(Run.id, Run.thread_id, Run.input, Run.started_at)
            )
        ).one()
        chat = (
            await conn.execute(
                select(
                    User.sub, Thread.parent_id, User.search_past_chats, User.remember
                )
                .join(Thread, Thread.user_id == User.id)
                .where(Thread.id == run.thread_id)
            )
        ).first()
        seq = await log.next_seq(conn, run_id)
        seq = await log.append(
            conn, run_id, seq, [RunEvent("run.started", {"attempt": attempt})]
        )
    return (
        StartedRun(
            user_sub=chat.sub if chat else "",
            parent_id=chat.parent_id if chat else None,
            search_past_chats=chat.search_past_chats if chat else True,
            remember=chat.remember if chat else True,
            **run._mapping,
        ),
        seq,
    )


@dataclass(frozen=True)
class Pending:
    """Something a paused turn asks the person for: its request's id and a LangGraph interrupt's
    value. An interrupt asked again in a later round keeps its id (a connector's server asking twice
    in one call, M9 4c-4), so each round has a request id of its own (`round_id`), and the answer
    resumes the interrupt by its own id."""

    id: str
    kind: str  # questions.kind_of
    request: dict[str, Any]
    interrupt_id: str | None = None  # the interrupt this answers, when it isn't `id`


def round_id(interrupt_id: str, round_: int) -> str:
    """The request id of an interrupt's `round_` (1 for its first question): the interrupt's own
    id, then `<id>-2`, `<id>-3`…"""
    return interrupt_id if round_ <= 1 else f"{interrupt_id}-{round_}"


def requested_event(pending: Pending) -> RunEvent:
    """`input.requested`: the request as clients show it (for a question, its `questions`)."""
    details = {k: v for k, v in pending.request.items() if k != "type"}
    return RunEvent(
        "input.requested", {"id": pending.id, "kind": pending.kind, **details}
    )


async def wait(
    conn: AsyncConnection, run_id: uuid.UUID, seq: int, pending: list[Pending]
) -> int:
    """Record what the run asks the person for, append `input.requested` for each new request
    and mark the run `waiting`, in the caller's transaction. Returns the next sequence number.
    A repeated attempt records nothing twice; a run that has ended stays ended."""
    current = await conn.scalar(
        select(Run.status).where(Run.id == run_id).with_for_update()
    )
    if current is None or current in FINAL_RUN_STATUSES:
        return seq
    new = set(
        await conn.scalars(
            pg_insert(InputRequest)
            .values(
                [
                    {"run_id": run_id, "id": p.id, "kind": p.kind, "request": p.request}
                    for p in pending
                ]
            )
            .on_conflict_do_nothing()
            .returning(InputRequest.id)
        )
    )
    seq = await log.append(
        conn, run_id, seq, [requested_event(p) for p in pending if p.id in new]
    )
    await conn.execute(update(Run).where(Run.id == run_id).values(status="waiting"))
    return seq


# A request the workflow makes when a turn failed in a way someone can fix: Retry answers it
RETRY_KIND = "retry"


async def park(engine: AsyncEngine, run_id: uuid.UUID, error: str) -> str:
    """Park a run whose turn failed in a way someone can fix, until its person retries: record a
    request of kind `retry`, append `input.requested` with the error in plain words, and mark the
    run `waiting` (the row keeps the error's detail). Returns the request's id. Repeating it (the
    Activity ran twice) records nothing twice."""
    async with engine.begin() as conn:
        current = await conn.scalar(
            select(Run.status).where(Run.id == run_id).with_for_update()
        )
        # One request per failed turn: after N earlier retries, this one is retry-(N+1), unless
        # the last one is still open (a repeated Activity)
        requests = list(
            await conn.execute(
                select(InputRequest.id, InputRequest.response)
                .where(InputRequest.run_id == run_id, InputRequest.kind == RETRY_KIND)
                .order_by(InputRequest.created_at)
            )
        )
        if requests and requests[-1].response is None:
            return requests[-1].id
        input_id = f"retry-{len(requests) + 1}"
        if current is None or current in FINAL_RUN_STATUSES:
            return input_id
        request = {"error": retry_reason(error)}
        await conn.execute(
            insert(InputRequest).values(
                run_id=run_id, id=input_id, kind=RETRY_KIND, request=request
            )
        )
        seq = await log.next_seq(conn, run_id)
        await log.append(
            conn,
            run_id,
            seq,
            [requested_event(Pending(input_id, RETRY_KIND, request))],
        )
        await conn.execute(
            update(Run).where(Run.id == run_id).values(status="waiting", error=error)
        )
    return input_id


async def responses(
    conn: AsyncConnection, run_id: uuid.UUID, ids: list[str]
) -> dict[str, dict[str, Any]] | None:
    """The person's responses to these requests of the run, by id; None while any is unanswered
    (or unknown)."""
    rows = await conn.execute(
        select(InputRequest.id, InputRequest.response).where(
            InputRequest.run_id == run_id, InputRequest.id.in_(ids)
        )
    )
    found = {row.id: row.response for row in rows}
    if any(found.get(i) is None for i in ids):
        return None
    return {i: found[i] for i in ids}


async def status(engine: AsyncEngine, run_id: uuid.UUID) -> str | None:
    """The run's status, or None if it is gone."""
    async with engine.connect() as conn:
        return await conn.scalar(select(Run.status).where(Run.id == run_id))


async def finish(
    conn: AsyncConnection, run_id: uuid.UUID, status: str, error: str | None = None
) -> None:
    await conn.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(status=status, error=error, finished_at=text("now()"))
    )


async def end(
    engine: AsyncEngine, run_id: uuid.UUID, status: str, error: str | None = None
) -> bool:
    """End a run that is still active with `status` ("cancelled", "error" or "expired": it waited
    for the person until its timeout), appending its
    `run.completed`. Returns False if it had already ended, so repeating it changes nothing."""
    async with engine.begin() as conn:
        current = await conn.scalar(
            select(Run.status).where(Run.id == run_id).with_for_update()
        )
        if current is None or current in FINAL_RUN_STATUSES:
            return False
        await finish(conn, run_id, status, error)
        seq = await log.next_seq(conn, run_id)
        await log.append(
            conn,
            run_id,
            seq,
            [
                RunEvent(
                    "run.completed",
                    {
                        "status": status,
                        "error": public_error(error) if status == "error" else None,
                    },
                )
            ],
        )
    return True
