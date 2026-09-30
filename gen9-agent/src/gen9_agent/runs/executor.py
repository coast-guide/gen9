"""Execute one attempt of a run: stream the agent, append its events, and record a success.

Runs as the `agent_turn` Temporal Activity (`runs/activities.py`). Every attempt passes the same
input with `metadata.run_id` set to the run's id. LangGraph stores that id in each checkpoint and
treats a call whose `run_id` matches the latest checkpoint as a re-entry into the same run
(`pregel/_loop.py`): a retry, after a worker died, resumes from the last checkpoint instead of
adding the message again. Finished steps are not repeated; a tool call cut off mid-way runs again
(explore/harness/NOTES.md, "Durable runs").

A turn can pause for the person (a question mid-task, questions.py): it ends at a LangGraph
interrupt, records what it asks (`store.wait`) and returns `waiting` with the interrupts' ids, and
the workflow waits for the answers. The next turn of the same run resumes with them
(`Command(resume=...)`); the API stored them and logged `input.provided`. Only interrupts this run
raised count (the checkpoint's `metadata.run_id`): a question an earlier run left unanswered is
patched over by the next message (explore/hitl/NOTES.md).

A cancelled or failed attempt flushes what it wrote and raises: the workflow decides whether to
retry, and records a cancellation or a final failure itself (`store.end`).
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import openai
from langchain_core.exceptions import ContextOverflowError
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.types import Command, StateSnapshot
from sqlalchemy import func, update
from temporalio.exceptions import ApplicationError

# The error type of a run whose person may no longer have work done (standing.py)
PERSON_INACTIVE = "PersonInactive"

from .. import (
    approvals,
    chat_files,
    elicitation,
    memory,
    notices,
    plugin_connectors,
    plugin_skills,
    questions,
)
from ..agent import langfuse_callbacks, tracing_attributes
from ..model_router import budget_exceeded, budget_of, on_behalf_of, over_rate_limit
from ..models import Thread
from ..runtime import Runtime
from ..standing import PersonInactive
from . import log, store
from .events import (
    MAX_TOOL_ARGS,
    USER_MESSAGE_PREFIX,
    EventMapper,
    RunEvent,
    json_safe,
)
from .store import Pending, StartedRun

logger = logging.getLogger(__name__)

DELTA_FLUSH_S = 0.1  # answer text is written at most ~10 times a second per run

# Failures a retry can't fix: the request itself is wrong (400, 404, 422), the key is refused
# (401, 403), the agent looped until LangGraph's recursion limit, or the user is over their model
# budget (budget_exceeded, below). Anything else is retried.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
    GraphRecursionError,
    # The turn doesn't fit in the context budget even after Deep Agents summarized the chat:
    # retrying sends the same (runs/store.py says so in words)
    ContextOverflowError,
)


@dataclass
class _Writer:
    """Appends events in order, merging consecutive text deltas of one message between flushes."""

    runtime: Runtime
    run_id: object
    seq: int
    pending: list[RunEvent] = field(default_factory=list)
    flushed_at: float = field(default_factory=time.monotonic)

    async def add(self, events: list[RunEvent]) -> None:
        for event in events:
            last = self.pending[-1] if self.pending else None
            if (
                event.type == "message.delta"
                and last is not None
                and last.type == "message.delta"
                and last.data["id"] == event.data["id"]
            ):
                self.pending[-1] = RunEvent(
                    "message.delta",
                    {
                        "id": last.data["id"],
                        "text": last.data["text"] + event.data["text"],
                    },
                )
            else:
                self.pending.append(event)
        if self.pending and (
            any(e.type != "message.delta" for e in self.pending)
            or time.monotonic() - self.flushed_at >= DELTA_FLUSH_S
        ):
            await self.flush()

    async def flush(self) -> None:
        if not self.pending:
            return
        events, self.pending = self.pending, []
        async with self.runtime.engine.begin() as conn:
            self.seq = await log.append(conn, self.run_id, self.seq, events)  # ty: ignore[invalid-argument-type]
        self.flushed_at = time.monotonic()

    async def wait(self, run: StartedRun, pending: list[Pending]) -> dict[str, Any]:
        """Flush, then record what the run asks the person for and mark it waiting, in one
        transaction. Returns the turn's result for the workflow: ids only."""
        events, self.pending = self.pending, []
        async with self.runtime.engine.begin() as conn:
            self.seq = await log.append(conn, run.id, self.seq, events)
            self.seq = await store.wait(conn, run.id, self.seq, pending)
        return {"status": "waiting", "pending": [p.id for p in pending]}

    async def succeed(self, run: StartedRun) -> None:
        """Flush, then record the run's success, its last event and the thread's title in one
        transaction."""
        events = [
            *self.pending,
            RunEvent("run.completed", {"status": "success", "error": None}),
        ]
        self.pending = []
        async with self.runtime.engine.begin() as conn:
            self.seq = await log.append(conn, run.id, self.seq, events)
            await store.finish(conn, run.id, "success")
            await conn.execute(
                update(Thread)
                .where(Thread.id == run.thread_id)
                .values(updated_at=func.now())
            )
            # Named when its run was queued (store.enqueue); a run queued before that still is here
            title = store.chat_title(run.input["message"])
            await conn.execute(
                update(Thread)
                .where(Thread.id == run.thread_id, Thread.title == "New chat")
                .values(title=title)
            )


def kind_of(value: Any) -> str:
    """What an interrupt asks the person for: a question (questions.py), an approval
    (approvals.py), a connector's server asking (elicitation.py), or something no client answers
    yet (it waits, then expires)."""
    if isinstance(value, dict) and value.get("type") == questions.INTERRUPT_TYPE:
        return questions.KIND
    if isinstance(value, dict) and value.get("action_requests"):
        return approvals.KIND
    if isinstance(value, dict) and value.get("type") == elicitation.INTERRUPT_TYPE:
        return elicitation.KIND
    return "unsupported"


# LangGraph keeps the answers a paused task has already consumed as its `__resume__` write
# (langgraph/_internal/_constants.py, RESUME; a test holds them equal): not imported, as internal
RESUME_WRITES = "__resume__"


async def rounds(runtime: Runtime, state: StateSnapshot) -> dict[str, int]:
    """Which round each interrupt is at: 1 + the answers its task already consumed. A task that
    asks again after an answer (a connector's server asking twice in one tool call) raises an
    interrupt with the same id (M9 4c-4). Read from the checkpoint, for the main graph's tasks
    and, in their own namespaces, a subagent's."""
    found: dict[str, int] = {}

    async def walk(snapshot: StateSnapshot) -> None:
        saved = await runtime.checkpointer.aget_tuple(snapshot.config)
        consumed: dict[str, int] = {}
        for task_id, channel, value in (saved.pending_writes if saved else None) or []:
            if channel == RESUME_WRITES:
                consumed[task_id] = len(value) if isinstance(value, list) else 1
        for task in snapshot.tasks:
            if isinstance(task.state, StateSnapshot):
                await walk(
                    task.state
                )  # the subagent's own task raised it, not this one
            for i in task.interrupts:
                found.setdefault(i.id, 1 + consumed.get(task.id, 0))

    await walk(state)
    return found


async def paused(
    runtime: Runtime, run: StartedRun, config: RunnableConfig
) -> list[Pending]:
    """The interrupts this run is paused at (a subagent's included), from the thread's latest
    checkpoint, each as its current round's request; none if the run isn't paused, or an earlier
    run left them."""
    state = await runtime.agent.aget_state(config, subgraphs=True)
    if not state.interrupts or (state.metadata or {}).get("run_id") != str(run.id):
        return []
    at = await rounds(runtime, state)
    # The tool calls the run is waiting on, to name the connector a server's question comes from
    calls = [
        c
        for m in (state.values or {}).get("messages", [])[-3:]
        for c in getattr(m, "tool_calls", None) or []
    ]

    def value_of(i: Any) -> dict[str, Any]:
        if not isinstance(i.value, dict):
            return {"value": json_safe(i.value, MAX_TOOL_ARGS)}
        if kind_of(i.value) == elicitation.KIND:
            return elicitation.described(i.value, calls)
        return i.value

    # Every interrupt pauses the run, even one no client can answer yet: it waits, then expires
    return [
        Pending(
            store.round_id(i.id, at.get(i.id, 1)),
            kind_of(i.value),
            value_of(i),
            interrupt_id=i.id,
        )
        for i in state.interrupts
    ]


async def execute(runtime: Runtime, run_id: uuid.UUID, attempt: int) -> dict[str, Any]:
    """Attempt `attempt` of the run's current turn. Returns `{"status", "pending"}`: "success";
    "waiting" with the ids of what it asks the person for; or "ended" when a repeated Activity
    finds the run already over."""
    started = await store.start(
        runtime.engine, run_id, attempt, runtime.definition.version
    )
    if started is None:
        # A repeated attempt of a turn that succeeded: the worker may have stopped between
        # recording the success and emailing its person. A notice is sent once, so send it again
        # (M9, F23; a failure's notice is finish_run's, which repeats the same way)
        if await store.status(runtime.engine, run_id) == "success":
            await notices.notify_safely(runtime, run_id, "done")
        return {"status": "ended", "pending": []}
    run, seq = started
    # Work for a person whose account is disabled or gone ends here, whatever started it (a
    # schedule, a trigger, a background task, a run left in the queue): standing.py
    if not await runtime.standing.active(run.user_sub):
        logger.info("run %s: its person's account is disabled or gone", run.id)
        raise ApplicationError(
            f"{PERSON_INACTIVE}: the account is disabled or gone",
            type=PERSON_INACTIVE,
            non_retryable=True,
        )
    writer = _Writer(runtime, run.id, seq)
    config: RunnableConfig = {
        "configurable": {"thread_id": str(run.thread_id)},
        "callbacks": langfuse_callbacks(),
        "metadata": {
            "run_id": str(run.id),
            "langfuse_user_id": run.user_sub,
            "langfuse_session_id": str(run.thread_id),
            # Which definition of the agent answered (definition.py)
            "agent_version": runtime.definition.version,
        },
    }
    try:
        # The connectors of the person's plugins, brought in line (plugin_connectors.py)
        async with runtime.sessionmaker() as session:
            await plugin_connectors.reconcile(runtime, session, None, run.user_sub)
        # The person's plugins' skills, for this turn (plugin_skills.py)
        theirs = await plugin_skills.load(
            runtime.engine, run.user_sub, runtime.definition.skill_names
        )
        mapper = EventMapper(theirs.plugin_of)
        # Every model call of the run is spent against the user's budget (model_router.py), and
        # every observation of its trace names the user and the chat
        # The person's memory file, for the agent to load and edit (memory.py)
        await memory.ensure_memory(runtime.store, run.user_sub, run.remember)
        # A turn after the person answered resumes with their answers; one whose answers aren't
        # all stored (a repeated attempt of a paused turn) keeps waiting
        waiting_for = await paused(runtime, run, config)
        # Files the message names, put in the environment before the turn (chat_files.py)
        attached = (
            []
            if waiting_for
            else await chat_files.deliver(
                runtime,
                run.thread_id,
                run.id,
                run.user_sub,
                run.input.get("files") or [],
            )
        )
        agent_input: Any = {
            # The run's id on its message: the chat's history knows which run answered it, and
            # so which answer shared which files (api/threads.py)
            "messages": [
                {
                    "role": "user",
                    "content": run.input["message"]
                    + chat_files.attached_note(attached),
                    "id": f"{USER_MESSAGE_PREFIX}{run.id}",
                }
            ],
            # Skills load again each turn: a plugin added or removed applies from this message
            "skills_metadata": None,
        }
        if waiting_for:
            async with runtime.engine.connect() as conn:
                answers = await store.responses(
                    conn, run.id, [p.id for p in waiting_for]
                )
            if answers is None:
                waited = await writer.wait(run, waiting_for)
                # A repeated attempt of a paused turn: its notice may not have gone (F23)
                await notices.notify_safely(runtime, run.id, "waiting")
                return waited
            # Each answer resumes its interrupt by the interrupt's own id: a later round's
            # request id isn't one LangGraph knows (store.round_id)
            agent_input = Command(
                resume={p.interrupt_id or p.id: answers[p.id] for p in waiting_for}
            )
        turn_started = time.monotonic()
        with (
            on_behalf_of(run.user_sub),
            tracing_attributes(run.user_sub, str(run.thread_id)),
        ):
            async for part in runtime.agent.astream(
                agent_input,
                config,
                # Whose memory: the store namespace of /memories/ (memory.store_backend)
                context=memory.Gen9Context(
                    user_sub=run.user_sub,
                    permission_mode=run.input.get(
                        "permission_mode", approvals.DEFAULT_MODE
                    ),
                    plugin_files=theirs.files,
                    environment_of=str(run.parent_id) if run.parent_id else None,
                    search_past_chats=run.search_past_chats,
                    remember=run.remember,
                ),
                stream_mode=["messages", "updates"],
                subgraphs=True,
                version="v2",
            ):
                await writer.add(mapper.map(part))
        waiting_for = await paused(runtime, run, config)
        # What the turn left in the environment's out folder, kept as the chat's files
        try:
            shared = await chat_files.capture(
                runtime, run.thread_id, run.id, run.user_sub, turn_started
            )
        except Exception as e:  # noqa: BLE001 (the answer stands without its files)
            logger.warning("run %s: files not captured: %s", run.id, e)
            shared = []
        if shared:
            await writer.add([RunEvent("files.shared", {"files": shared})])
    except asyncio.CancelledError:
        # Stop, or the worker is shutting down: keep what was written, then let Temporal decide
        await asyncio.shield(writer.flush())
        raise
    except PersonInactive as e:
        # Disabled or deleted mid-turn (standing.StillActive): as at the turn's start
        await writer.flush()
        logger.info("run %s: its person's account was disabled or deleted", run.id)
        raise ApplicationError(
            f"{PERSON_INACTIVE}: the account is disabled or gone",
            type=PERSON_INACTIVE,
            non_retryable=True,
        ) from e
    except Exception as e:
        await writer.flush()
        if budget_exceeded(e):
            logger.info("run %s: its user is over their model budget", run.id)
            # When it resets, kept in the error itself: the run's row keeps the innermost
            # cause, so it isn't chained (store.public_error words it; P5-D1)
            budget = await budget_of(runtime.settings, run.user_sub) or {}
            resets = (
                f" (resets {budget['resets_at']})" if budget.get("resets_at") else ""
            )
            raise ApplicationError(
                f"{store.BUDGET_EXCEEDED}: {e}{resets}",
                type=store.BUDGET_EXCEEDED,
                non_retryable=True,
            ) from None
        if over_rate_limit(e):
            logger.info("run %s: its user is over their requests per minute", run.id)
            raise ApplicationError(
                f"{store.RATE_LIMITED}: {e}",
                type=store.RATE_LIMITED,
                non_retryable=True,
            ) from e
        if isinstance(e, PERMANENT_ERRORS):
            logger.warning("run %s failed for good: %s", run.id, e)
            raise ApplicationError(
                f"{type(e).__name__}: {e}", type=type(e).__name__, non_retryable=True
            ) from e
        logger.exception("run %s: attempt %d failed", run.id, attempt)
        raise
    if waiting_for:
        logger.info("run %s: waiting for the person (%d)", run.id, len(waiting_for))
        waited = await writer.wait(run, waiting_for)
        # A background run asking something tells its person (notices.py)
        await notices.notify_safely(runtime, run.id, "waiting")
        return waited
    await writer.succeed(run)
    await notices.notify_safely(runtime, run.id, "done")
    return {"status": "success", "pending": []}
