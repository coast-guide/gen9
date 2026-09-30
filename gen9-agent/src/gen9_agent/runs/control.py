"""Starting, answering and stopping runs, for the API: the row in Postgres, the workflow in
Temporal."""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import Client
from temporalio.common import (
    Priority,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError, RPCStatusCode

from .. import approvals, elicitation, questions
from ..models import ACTIVE_RUN_STATUSES, InputRequest, Run, Thread, User
from ..temporal import GEN9_KIND, GEN9_THREAD, GEN9_USER
from ..workflows.names import (
    ANSWERED_SIGNAL,
    PRIORITY_CHAT,
    SYSTEM_QUEUE,
    run_workflow_id,
)
from ..workflows.runs import AnswerInput, RunInput, RunWorkflow
from . import log as event_log
from . import store
from .events import RunEvent

log = logging.getLogger(__name__)

# How long sending the `answered` Signal may take
SIGNAL_TIMEOUT_S = 10


class InputNotFound(Exception):
    """The run asked for no such thing."""


class AlreadyAnswered(Exception):
    """Someone answered it first."""


class NotWaiting(Exception):
    """The run no longer waits for an answer (stopped, expired, or over)."""


async def start_run(
    engine: AsyncEngine,
    temporal: Client,
    thread_id: uuid.UUID,
    user_sub: str,
    input: dict[str, Any],
    priority: int = PRIORITY_CHAT,
    wait_s: int | None = None,
    notify: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record the run (raises store.ActiveRunExists when the thread is busy), then start its
    workflow. The workflow gets IDs only; the message stays in Postgres. `wait_s`: how long the
    run may wait for the person when it asks something (None: the workflow's default).
    `notify`: a background task's run, the chat to tell when it ends (background.py)."""
    run_id = await store.enqueue(engine, thread_id, input)
    try:
        await temporal.start_workflow(
            RunWorkflow.run,
            RunInput(
                run_id=str(run_id),
                user_sub=user_sub,
                wait_s=wait_s,
                notify=str(notify) if notify else None,
            ),
            id=run_workflow_id(str(run_id)),
            task_queue=SYSTEM_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            search_attributes=TypedSearchAttributes(
                [
                    SearchAttributePair(GEN9_USER, user_sub),
                    SearchAttributePair(GEN9_THREAD, str(thread_id)),
                    SearchAttributePair(GEN9_KIND, "run"),
                ]
            ),
            # Runs someone waits for go first; each person's runs share the queue fairly
            priority=Priority(priority_key=priority, fairness_key=user_sub),
        )
    except Exception as e:
        await store.end(
            engine, run_id, "error", f"not started: {type(e).__name__}: {e}"
        )
        raise
    return run_id


async def answer(
    engine: AsyncEngine,
    temporal: Client,
    run_id: uuid.UUID,
    input_id: str,
    user_sub: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Answer what the waiting run asks for, as its person: `{"answers": [...]}` for a question,
    `{"decisions": [...]}` for an approval. First, in one transaction: check the response against
    the request, store it (the first answer wins) and log `input.provided`.
    Then send the run's workflow the `answered` Signal, with IDs only, which Temporal records even
    while no worker runs (explore/hitl/NOTES.md). Returns the response as stored.

    Raises InputNotFound; AlreadyAnswered, after signalling again in case the first Signal was
    lost; NotWaiting; ValueError, for a response that doesn't fit the request; or an RPC error when
    Temporal can't be reached (the answer is stored, and answering again delivers it)."""
    async with engine.begin() as conn:
        status = await conn.scalar(
            select(Run.status).where(Run.id == run_id).with_for_update()
        )
        request = (
            await conn.execute(
                select(InputRequest.kind, InputRequest.request, InputRequest.response)
                .where(InputRequest.run_id == run_id, InputRequest.id == input_id)
                .with_for_update()
            )
        ).first()
        if request is None:
            raise InputNotFound
        already = request.response is not None
        if not already:
            if status != "waiting":
                raise NotWaiting
            cleaned = _checked(request.kind, request.request, response)
            await conn.execute(
                update(InputRequest)
                .where(InputRequest.run_id == run_id, InputRequest.id == input_id)
                .values(response=cleaned, answered_at=func.now())
            )
            seq = await event_log.next_seq(conn, run_id)
            await event_log.append(
                conn,
                run_id,
                seq,
                [RunEvent("input.provided", {"id": input_id, **cleaned})],
            )
    if already and status != "waiting":
        raise AlreadyAnswered
    handle = temporal.get_workflow_handle(run_workflow_id(str(run_id)))
    try:
        await handle.signal(
            ANSWERED_SIGNAL,
            AnswerInput(input_id=input_id, user_sub=user_sub),
            rpc_timeout=timedelta(seconds=SIGNAL_TIMEOUT_S),
        )
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
        # No workflow (it ended): the stored answer stays with the request, unused
        log.warning("run %s: answered, but its workflow is gone", run_id)
    if already:
        raise AlreadyAnswered
    return cleaned


def _checked(
    kind: str, request: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    """The person's response to a request of this kind, cleaned, or ValueError."""
    if kind == questions.KIND and response.get("answers") is not None:
        return {"answers": questions.check_answers(request, response["answers"])}
    if kind == approvals.KIND and response.get("decisions") is not None:
        return {"decisions": approvals.check_decisions(request, response["decisions"])}
    if kind == store.RETRY_KIND and response.get("retry") is True:
        return {"retry": True}
    if kind == elicitation.KIND and response.get("responses") is not None:
        # What langchain.mcp resumes the tool call with
        return {
            "responses": elicitation.check_responses(request, response["responses"])
        }
    wanted = {
        questions.KIND: "answers",
        approvals.KIND: "decisions",
        store.RETRY_KIND: "retry",
        elicitation.KIND: "responses",
    }.get(kind)
    raise ValueError(
        f"this request takes {wanted}"
        if wanted
        else "this request can't be answered yet"
    )


async def stop_run(engine: AsyncEngine, temporal: Client, run_id: uuid.UUID) -> None:
    """Cancel the run's workflow; its Activity stops at its next heartbeat and the workflow records
    the cancellation. A run whose workflow doesn't exist is ended here."""
    try:
        await temporal.get_workflow_handle(run_workflow_id(str(run_id))).cancel()
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
        await store.end(engine, run_id, "cancelled")


async def stop_runs_of(
    engine: AsyncEngine, temporal: Client, user_sub: str | None, wait_s: float = 0
) -> int:
    """Stop every active run (queued, running or waiting) of a person, or of everyone (`None`:
    the operator's stop, stop.py); how many were stopped. Each ends as cancelled once its
    workflow records it, which `wait_s` waits for, at most (docs/plans/manual-e2e.md,
    P5-C10)."""
    query = (
        select(Run.id)
        .join(Thread, Thread.id == Run.thread_id)
        .where(Run.status.in_(ACTIVE_RUN_STATUSES))
    )
    if user_sub is not None:
        query = query.join(User, User.id == Thread.user_id).where(User.sub == user_sub)
    async with engine.connect() as conn:
        runs = list(await conn.scalars(query))
    for run_id in runs:
        await stop_run(engine, temporal, run_id)
    if runs and wait_s:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(wait_s):
                while True:
                    async with engine.connect() as conn:
                        left = await conn.scalar(
                            select(func.count()).where(
                                Run.id.in_(runs), Run.status.in_(ACTIVE_RUN_STATUSES)
                            )
                        )
                    if not left:
                        break
                    await asyncio.sleep(0.5)
    return len(runs)


async def stop_thread_runs(
    engine: AsyncEngine,
    temporal: Client,
    thread_ids: Iterable[uuid.UUID],
    wait_s: float = 60,
) -> None:
    """Stop the active runs of these threads and wait until they have ended (their workflow
    records the end), so nothing is written after a deletion. Raises TimeoutError if a run is
    still active after `wait_s`; a deletion Activity then retries."""
    threads = list(thread_ids)
    stopped: set[uuid.UUID] = set()
    async with asyncio.timeout(wait_s):
        while True:
            async with engine.connect() as conn:
                active = set(
                    await conn.scalars(
                        select(Run.id).where(
                            Run.thread_id.in_(threads),
                            Run.status.in_(ACTIVE_RUN_STATUSES),
                        )
                    )
                )
            if not active:
                return
            for run_id in active - stopped:
                await stop_run(engine, temporal, run_id)
                stopped.add(run_id)
            await asyncio.sleep(0.5)
