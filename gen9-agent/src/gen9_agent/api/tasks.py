"""A person's scheduled tasks (tasks.py): what Gen9 runs on its own, each time in a new chat. Only
their owner sees or changes one; others get 404."""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from .. import audit, outcomes, tasks
from ..deps import Session
from ..models import OutcomeEvaluation, Run, Task, Thread, User
from ..not_blank import TASK_NAME, TASK_PROMPT
from ..users import CurrentUser

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])
Mode = Literal["ask", "auto"]
RECENT_RUNS = 5
# What done looks like (outcomes.py): a Markdown rubric, and how many runs a firing may take
Rubric = Annotated[str | None, Field(max_length=8000)]
Tries = Annotated[int, Field(ge=1, le=outcomes.MAX_ITERATIONS)]
# A run of a task with a rubric that finished this recently, ungraded, is still being checked
CHECKING = timedelta(minutes=10)


class ScheduleIn(BaseModel):
    kind: Literal["once", "hourly", "daily", "weekdays", "weekly"]
    time: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
    weekday: Annotated[int | None, Field(ge=0, le=6)] = None  # Monday first
    date: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None


class TaskIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80), TASK_NAME]
    prompt: Annotated[str, Field(min_length=1, max_length=8000), TASK_PROMPT]
    schedule: ScheduleIn
    time_zone: Annotated[str, Field(min_length=1, max_length=64)]
    permission_mode: Mode = "auto"
    rubric: Rubric = None
    max_iterations: Tries = outcomes.DEFAULT_ITERATIONS


class TaskPatch(BaseModel):
    name: Annotated[str | None, Field(min_length=1, max_length=80), TASK_NAME] = None
    prompt: Annotated[str | None, Field(min_length=1, max_length=8000), TASK_PROMPT] = (
        None
    )
    schedule: ScheduleIn | None = None
    time_zone: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    permission_mode: Mode | None = None
    # An empty rubric removes it
    rubric: Rubric = None
    max_iterations: Tries | None = None


class TaskRunOut(BaseModel):
    thread_id: uuid.UUID
    status: str | None  # its latest run's: queued, running, waiting, success, error, …
    created_at: datetime
    # With a rubric: the latest verdict in the chat (satisfied, needs_revision or failed),
    # and how many of its runs were graded
    outcome: str | None = None
    graded: int = 0
    # Its latest run just finished and is being graded (or revised): not the final word yet
    checking: bool = False


class TaskOut(BaseModel):
    id: uuid.UUID
    name: str
    prompt: str
    schedule: dict[str, Any]
    schedule_words: str
    time_zone: str
    permission_mode: str
    rubric: str | None
    max_iterations: int
    status: str  # active, paused or done
    next_at: datetime | None
    skipped: int  # firings skipped because the last run was still going
    runs: list[TaskRunOut]
    # It has an API trigger (a token); the address to POST to
    has_trigger: bool
    fire_url: str
    created_at: datetime


class TriggerOut(BaseModel):
    """A task's new API trigger: the token is shown this once, and only its hash is kept."""

    url: str
    token: str


class FireIn(BaseModel):
    # Run-specific context (an alert, a log line): reaches the run labelled as data
    text: Annotated[str | None, Field(max_length=65536)] = None


class Fired(BaseModel):
    task_id: uuid.UUID


async def _out(task: Task, session: Session, request: Request) -> TaskOut:
    latest = (
        select(Run.status)
        .where(Run.thread_id == Thread.id)
        .order_by(Run.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    # The chat's verdicts: the latest, and how many
    outcome = (
        select(OutcomeEvaluation.result)
        .join(Run, Run.id == OutcomeEvaluation.run_id)
        .where(Run.thread_id == Thread.id)
        .order_by(OutcomeEvaluation.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    tries = (
        select(func.count())
        .select_from(OutcomeEvaluation)
        .join(Run, Run.id == OutcomeEvaluation.run_id)
        .where(Run.thread_id == Thread.id)
        .scalar_subquery()
    )
    newer = aliased(Run)
    checking = (
        select(Run.id)
        .where(
            Run.thread_id == Thread.id,
            Run.status == "success",
            Run.finished_at > func.now() - CHECKING,
            ~select(OutcomeEvaluation.run_id)
            .where(OutcomeEvaluation.run_id == Run.id)
            .exists(),
            ~select(newer.id)
            .where(newer.thread_id == Thread.id, newer.created_at > Run.created_at)
            .exists(),
        )
        .exists()
    )
    runs = await session.execute(
        select(Thread.id, latest, Thread.created_at, outcome, tries, checking)
        .where(Thread.task_id == task.id, Thread.deleted_at.is_(None))
        .order_by(Thread.created_at.desc())
        .limit(RECENT_RUNS)
    )
    ahead = await tasks.upcoming(request.app.state.temporal, task)
    return TaskOut(
        id=task.id,
        name=task.name,
        prompt=task.prompt,
        schedule=task.schedule,
        schedule_words=tasks.in_words(task.schedule, task.time_zone),
        time_zone=task.time_zone,
        permission_mode=task.permission_mode,
        rubric=task.rubric,
        max_iterations=task.max_iterations,
        status=task.status,
        next_at=ahead.next_at,
        skipped=ahead.skipped,
        runs=[
            TaskRunOut(
                thread_id=t,
                status=s,
                created_at=c,
                outcome=o,
                graded=g,
                checking=bool(task.rubric) and k,
            )
            for t, s, c, o, g, k in runs.tuples()
        ],
        has_trigger=task.trigger_hash is not None,
        fire_url=_fire_url(request, task.id),
        created_at=task.created_at,
    )


def _fire_url(request: Request, task_id: uuid.UUID) -> str:
    base = request.app.state.runtime.settings.gen9_api_public_url.rstrip("/")
    return f"{base}/v1/tasks/{task_id}/fire"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _within_limits(session: Session, task: Task, request: Request) -> None:
    """Records a fire outside the schedule, or 429 with when to try again, past the hourly
    limits (per task and per person, as Claude Code's routines)."""
    settings = request.app.state.runtime.settings
    wait = await tasks.record_fire(
        session,
        task.id,
        task.user_id,
        settings.tasks_fires_per_hour,
        settings.tasks_fires_per_person_hour,
    )
    if wait is not None:
        await session.rollback()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "It has run as often as it may this hour.",
            headers={"Retry-After": str(wait)},
        )
    await session.commit()


async def _owned(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Task:
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    if task is None:
        await audit.theirs(request, session, Task, task_id, user, "task")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such task")
    return task


async def _checked(schedule: ScheduleIn, time_zone: str) -> tuple[dict[str, Any], str]:
    """The schedule, and the time zone's canonical name."""
    try:
        canonical = await tasks.zone(time_zone)
        return tasks.checked(schedule.model_dump(exclude_none=True)), canonical
    except tasks.TaskError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


async def _arrange(
    task: Task, user: CurrentUser, session: Session, request: Request
) -> None:
    """Temporal made to fire it as it now says; nothing kept if that's refused."""
    try:
        await tasks.arrange(request.app.state.temporal, task, user.sub)
    except tasks.TaskError as e:
        await session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


@router.get("", summary="Your scheduled tasks, with their next and latest runs")
async def list_tasks(
    user: CurrentUser, session: Session, request: Request
) -> list[TaskOut]:
    rows = await session.scalars(
        select(Task).where(Task.user_id == user.id).order_by(Task.created_at)
    )
    return [await _out(t, session, request) for t in rows]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Schedule a task")
async def add_task(
    body: TaskIn, user: CurrentUser, session: Session, request: Request
) -> TaskOut:
    cap = request.app.state.runtime.settings.tasks_max_per_person
    kept = await session.scalar(
        select(func.count()).where(Task.user_id == user.id, Task.status != "done")
    )
    if (kept or 0) >= cap:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"At most {cap} tasks at a time. Delete one first.",
        )
    schedule, time_zone = await _checked(body.schedule, body.time_zone)
    task = Task(
        user_id=user.id,
        name=body.name.strip(),
        prompt=body.prompt.strip(),
        schedule=schedule,
        time_zone=time_zone,
        permission_mode=body.permission_mode,
        rubric=(body.rubric or "").strip() or None,
        max_iterations=body.max_iterations,
    )
    session.add(task)
    await session.flush()
    await session.refresh(task)
    await _arrange(task, user, session, request)
    await session.commit()
    return await _out(task, session, request)


@router.patch(
    "/{task_id}",
    summary="Change a task: its name, message, schedule, mode or rubric",
)
async def change_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    user: CurrentUser,
    session: Session,
    request: Request,
) -> TaskOut:
    task = await _owned(task_id, user, session, request)
    if body.name is not None:
        task.name = body.name.strip()
    if body.prompt is not None:
        task.prompt = body.prompt.strip()
    if body.permission_mode is not None:
        task.permission_mode = body.permission_mode
    if body.rubric is not None:
        task.rubric = body.rubric.strip() or None
    if body.max_iterations is not None:
        task.max_iterations = body.max_iterations
    if body.time_zone is not None or body.schedule is not None:
        task.schedule, task.time_zone = await _checked(
            body.schedule or ScheduleIn(**task.schedule),
            body.time_zone or task.time_zone,
        )
        if task.status == "done":  # a one-off given a new time runs again
            task.status = "active"
    task.updated_at = func.now()
    await session.flush()
    await session.refresh(task)
    await _arrange(task, user, session, request)
    await session.commit()
    return await _out(task, session, request)


@router.post(
    "/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a task now, in a new chat, whatever its schedule says",
)
async def run_task(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> TaskOut:
    task = await _owned(task_id, user, session, request)
    await _within_limits(session, task, request)
    await tasks.run_now(request.app.state.temporal, task, user.sub)
    return await _out(task, session, request)


@router.post(
    "/{task_id}/trigger",
    status_code=status.HTTP_201_CREATED,
    summary="Make the task's API trigger: its token, shown this once (a new one revokes the old)",
)
async def make_trigger(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> TriggerOut:
    task = await _owned(task_id, user, session, request)
    token = f"gen9_trigger_{secrets.token_urlsafe(32)}"
    task.trigger_hash = _hash(token)
    task.trigger_made_at = func.now()
    await session.commit()
    await audit.record(request, user.sub, "task.trigger.make", target=task.id)
    return TriggerOut(url=_fire_url(request, task.id), token=token)


@router.delete(
    "/{task_id}/trigger",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the task's API trigger",
)
async def revoke_trigger(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Response:
    task = await _owned(task_id, user, session, request)
    task.trigger_hash = None
    task.trigger_made_at = None
    await session.commit()
    await audit.record(request, user.sub, "task.trigger.revoke", target=task.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{task_id}/fire",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fire a task with its API trigger's token (no sign-in): a new chat runs it",
    responses={
        401: {"description": "No token, or not this task's"},
        409: {"description": "The task is paused"},
        429: {"description": "Over the hourly limit; Retry-After says when"},
    },
)
async def fire_task(
    task_id: uuid.UUID,
    session: Session,
    request: Request,
    body: FireIn | None = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Fired:
    """The token is the task's only credential: it fires this task and nothing else. The text
    reaches the run inside a block that labels it as data from the caller (tasks.with_payload)."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    row = (
        await session.execute(
            select(Task, User.sub)
            .join(User, User.id == Task.user_id)
            .where(Task.id == task_id)
        )
    ).first()
    known = row[0].trigger_hash if row else None
    if (
        row is None
        or not token
        or not known
        or not hmac.compare_digest(_hash(token), known)
    ):
        # The same answer whether the task exists or not
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "That token doesn't fire this task.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    task, sub = row[0], row[1]
    if task.status == "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, "The task is paused.")
    # Its person's account is disabled or gone: nothing is done for them (standing.py)
    if not await request.app.state.runtime.standing.active(sub):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This task's person can't use Gen9 right now."
        )
    await _within_limits(session, task, request)
    text = body.text if body else None
    await tasks.run_now(request.app.state.temporal, task, sub, text)
    return Fired(task_id=task.id)


async def _pause(
    task_id: uuid.UUID,
    paused: bool,
    user: CurrentUser,
    session: Session,
    request: Request,
) -> TaskOut:
    task = await _owned(task_id, user, session, request)
    if task.schedule["kind"] == "once":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A task that runs once can't be paused: change its time, or delete it.",
        )
    await tasks.pause(request.app.state.temporal, task, paused)
    task.status = "paused" if paused else "active"
    task.updated_at = func.now()
    await session.commit()
    await session.refresh(task)
    return await _out(task, session, request)


@router.post("/{task_id}/pause", summary="Pause a recurring task")
async def pause_task(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> TaskOut:
    return await _pause(task_id, True, user, session, request)


@router.post("/{task_id}/resume", summary="Resume a paused task")
async def resume_task(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> TaskOut:
    return await _pause(task_id, False, user, session, request)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task: it stops firing; the chats it made stay",
)
async def delete_task(
    task_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Response:
    task = await _owned(task_id, user, session, request)
    await tasks.unarrange(request.app.state.temporal, task.id)
    await session.delete(task)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
