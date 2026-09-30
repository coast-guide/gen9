"""Runs of a thread: recorded here, executed by Temporal workers, streamed from their event log.

A run keeps going when the client disconnects; `GET …/stream` replays its events from the
`Last-Event-ID` the client last saw (or `?after=`) and follows it live until `run.completed`.
Only the thread's owner sees its runs (others get 404, as for threads).
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from .. import approvals, audit, questions
from ..auth import CurrentPrincipal
from ..models import ChatFile, Run, Thread
from ..runs import control, log, store
from ..users import CurrentUser
from .threads import OwnedThread, RunIn

router = APIRouter(prefix="/v1/threads", tags=["runs"])
logger = logging.getLogger(__name__)

# How long a stream waits for new events before checking again (notifications usually wake it)
WAIT_S = 15


class RunOut(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    status: str
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # The version of the agent's definition that answered (definition.py); none before it started
    agent_version: str | None = None


def run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        agent_version=run.agent_version,
        attempts=run.attempts,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def owned_run(
    thread: OwnedThread, run_id: uuid.UUID, user: CurrentUser, request: Request
) -> Run:
    async with request.app.state.sessionmaker() as session:
        run = await session.scalar(
            select(Run).where(Run.id == run_id, Run.thread_id == thread.id)
        )
        if run is None:
            await audit.theirs_through(
                request,
                session,
                select(Thread.user_id)
                .join(Run, Run.thread_id == Thread.id)
                .where(Run.id == run_id),
                run_id,
                user,
                "run",
            )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


async def queued_run(
    thread: OwnedThread, body: RunIn, principal: CurrentPrincipal, request: Request
) -> uuid.UUID:
    """Start the run before any response is sent, so a busy thread gets a plain 409."""
    if thread.parent_id is not None:
        # A background task's chat takes its instructions from the chat that started it
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This chat is a background task. Talk to it through the chat that started it.",
        )
    if body.files:
        async with request.app.state.sessionmaker() as session:
            found = await session.scalar(
                select(func.count())
                .select_from(ChatFile)
                .where(
                    ChatFile.thread_id == thread.id,
                    ChatFile.origin == "upload",
                    ChatFile.id.in_(body.files),
                )
            )
        if found != len(set(body.files)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Attach files to this chat before naming them.",
            )
    try:
        return await control.start_run(
            request.app.state.engine,
            request.app.state.temporal,
            thread.id,
            principal.sub,
            {
                "message": body.message,
                "permission_mode": body.permission_mode or thread.permission_mode,
                **(
                    {"files": [str(f) for f in dict.fromkeys(body.files)]}
                    if body.files
                    else {}
                ),
            },
            wait_s=request.app.state.runtime.settings.run_wait_s,
        )
    except store.ActiveRunExists:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This chat is still answering. Wait or stop it first.",
        ) from None
    except Exception:
        logger.exception("could not start a run on thread %s", thread.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The agent can't start right now. Try again in a moment.",
        ) from None


OwnedRun = Annotated[Run, Depends(owned_run)]
QueuedRun = Annotated[uuid.UUID, Depends(queued_run)]


async def _events(
    request: Request, run_id: uuid.UUID, after: int
) -> AsyncIterable[ServerSentEvent]:
    """Replay the run's events after `after`, then follow them live until `run.completed`."""
    engine, hub = request.app.state.engine, request.app.state.event_hub
    while True:
        rows = await log.read_after(engine, run_id, after)
        for seq, type_, data in rows:
            yield ServerSentEvent(event=type_, data=data, id=str(seq))
            after = seq
            if type_ == "run.completed":
                return
        if not rows:
            if await request.is_disconnected():
                return
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(WAIT_S):
                    await hub.wait(run_id)


def _resume_point(last_event_id: str | None, after: int | None) -> int:
    for value in (last_event_id, after):
        if value is not None and str(value).isdigit():
            return int(value)
    return 0


@router.get("/{thread_id}/runs", summary="The thread's runs, most recent first")
async def list_runs(thread: OwnedThread, request: Request) -> list[RunOut]:
    async with request.app.state.sessionmaker() as session:
        runs = await session.scalars(
            select(Run)
            .where(Run.thread_id == thread.id)
            .order_by(Run.created_at.desc())
            .limit(50)
        )
        return [run_out(r) for r in runs]


@router.post(
    "/{thread_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a message as a run in the background; follow it at …/runs/{run}/stream",
)
async def create_run(run_id: QueuedRun, request: Request) -> RunOut:
    async with request.app.state.sessionmaker() as session:
        return run_out(await session.get_one(Run, run_id))


@router.post(
    "/{thread_id}/runs/stream",
    response_class=EventSourceResponse,
    summary="Send a message and stream the run's events (SSE); the run continues if you leave",
)
async def create_and_stream(
    run_id: QueuedRun, request: Request
) -> AsyncIterable[ServerSentEvent]:
    async for event in _events(request, run_id, 0):
        yield event


@router.get("/{thread_id}/runs/{run_id}", summary="A run's status")
async def get_run(run: OwnedRun) -> RunOut:
    return run_out(run)


@router.get(
    "/{thread_id}/runs/{run_id}/stream",
    response_class=EventSourceResponse,
    summary="Replay a run's events after Last-Event-ID (or ?after=), then follow it live",
)
async def stream_run(
    run: OwnedRun,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> AsyncIterable[ServerSentEvent]:
    async for event in _events(request, run.id, _resume_point(last_event_id, after)):
        yield event


@router.post(
    "/{thread_id}/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stop a run (a running one stops within about half a second)",
)
async def cancel_run(run: OwnedRun, request: Request) -> RunOut:
    await control.stop_run(request.app.state.engine, request.app.state.temporal, run.id)
    async with request.app.state.sessionmaker() as session:
        return run_out(await session.get_one(Run, run.id))


class Decision(BaseModel):
    type: Literal["approve", "reject"]
    # With a reject: what the agent should do instead (it reads this as the person's reason)
    message: str | None = Field(default=None, max_length=approvals.MAX_REASON_CHARS)


class ElicitationAnswer(BaseModel):
    """One answer to a connector's server: accept (with a form's fields), decline or cancel."""

    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


class AnswerIn(BaseModel):
    """For a question, one answer per question, in order ("" leaves an optional one unanswered);
    for an approval, one decision per action; for a connector's server asking, one answer per
    request by its key; for a failed run, `retry`."""

    answers: (
        list[Annotated[str, Field(max_length=questions.MAX_ANSWER_CHARS)]] | None
    ) = Field(default=None, min_length=1, max_length=questions.MAX_QUESTIONS)
    decisions: list[Decision] | None = Field(default=None, min_length=1, max_length=20)
    # For a connector's server asking (elicitation.py): an answer per request, by its key
    responses: dict[str, ElicitationAnswer] | None = Field(default=None, max_length=20)
    # For a run that failed and waits: try its turn again, from its checkpoint
    retry: Literal[True] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "AnswerIn":
        given = [
            x
            for x in (self.answers, self.decisions, self.responses, self.retry)
            if x is not None
        ]
        if len(given) != 1:
            raise ValueError("send one of answers, decisions, responses or retry")
        return self


class AnswerOut(BaseModel):
    id: str
    answers: list[str] | None = None
    decisions: list[dict[str, Any]] | None = None
    retry: bool | None = None


@router.post(
    "/{thread_id}/runs/{run_id}/inputs/{input_id}",
    summary="Answer what a waiting run asks (its `input.requested` event); the run then goes on",
    response_model_exclude_none=True,
    responses={
        404: {"description": "No such run or request (or not yours)"},
        409: {"description": "Already answered, or the run no longer waits"},
        422: {"description": "The answers or decisions don't fit the request"},
    },
)
async def answer_input(
    run: OwnedRun,
    input_id: Annotated[str, Field(max_length=64)],
    body: AnswerIn,
    principal: CurrentPrincipal,
    request: Request,
) -> AnswerOut:
    try:
        stored = await control.answer(
            request.app.state.engine,
            request.app.state.temporal,
            run.id,
            input_id,
            principal.sub,
            body.model_dump(exclude_none=True),
        )
    except control.InputNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such question") from None
    except control.AlreadyAnswered:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already answered") from None
    except control.NotWaiting:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This run is no longer waiting for an answer"
        ) from None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    return AnswerOut(id=input_id, **stored)
