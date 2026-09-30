"""`RunWorkflow`: one run (one turn of a chat) from queued to its end.

The turn itself is one long Activity, `agent_turn`, which streams the agent, appends its events to
the run's log in Postgres and heartbeats every half second. If its worker dies, Temporal notices within
the 3 s heartbeat timeout and retries it on another worker, where LangGraph resumes the run from
its last checkpoint. Cancelling this workflow (Stop) reaches the Activity with its next heartbeat.
A run that is cancelled, or fails for good, is ended here by `finish_run`, which also covers a run
cancelled before its Activity started. Design: docs/plans/harness.md, "how runs move onto Temporal".

A turn that fails in a way someone can fix from outside (its retries spent on a provider that is
down or out of credits, or a usage limit that resets) parks the run instead of ending it: the
`park_run` Activity records a request of kind `retry`, and the run waits for the person's Retry,
then runs the turn again from its checkpoint (Temporal's Resumable Activity pattern, behind
`patched("park-failed-turn")`). A failure a retry can't fix ends the run as `error`.

A turn may pause for the person (a question mid-task): `agent_turn` then returns `waiting` with the
ids of what it asks for. The workflow waits, durably and for up to 7 days, until each has been
answered, then runs the next turn, which resumes where the agent paused. The API stores and checks
each answer in Postgres, where the first one wins, and only then sends the `answered` Signal with
IDs only (docs/temporal.md, rules 2 and 8). A Signal is recorded even while no worker runs, where
an Update whose call timed out may still land later (explore/hitl/NOTES.md). No answer in time
ends the run as `expired`, and Stop while waiting cancels it as usual.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    is_cancelled_exception,
)
from temporalio.exceptions import TimeoutError as ActivityTimeout
from temporalio.workflow import ParentClosePolicy

from .background import TellChat, TellChatWorkflow
from .names import (
    AGENT_QUEUE,
    AGENT_TURN,
    ANSWERED_SIGNAL,
    BUDGET_EXCEEDED,
    FINISH_RUN,
    INDEX_RUN,
    PARK_RUN,
    RATE_LIMITED,
    SYSTEM_QUEUE,
    tell_chat_workflow_id,
)

# How a background task's run may end for its chat to be told (a cancel isn't)
TOLD = ("success", "error", "expired")


@dataclass(frozen=True)
class RunInput:
    run_id: str
    user_sub: str
    # How long a pause for the person may last, in seconds; None: WAIT_FOR_PERSON. Added
    # later, so inputs recorded before have none.
    wait_s: int | None = None
    # A background task's run: the chat to tell when it ends (background.py). Added later,
    # so inputs recorded before have none.
    notify: str | None = None


@dataclass(frozen=True)
class FinishInput:
    run_id: str
    status: str  # "cancelled", "error" or "expired"
    error: str | None = None


@dataclass(frozen=True)
class ParkInput:
    run_id: str
    error: (
        str  # the innermost cause, for the run's row; the person sees it in plain words
    )


@dataclass(frozen=True)
class AnswerInput:
    """The `answered` Signal: which request of the run was answered, and by whom. The answer
    itself stays in Postgres (`run_inputs`)."""

    input_id: str
    user_sub: str


# Transient failures (a lost worker, a timeout, a 429 or 5xx) are retried and resume from the
# checkpoint; permanent ones are raised non-retryable by the Activity and end the run at once
TURN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


# Making a finished run searchable: its text right away, its embedding through the model router,
# which may be down for a while (runs/indexing.py)
INDEX_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=15,
)

# How long a run waits for the person by default (docs/temporal.md, "Timeouts")
WAIT_FOR_PERSON = timedelta(days=7)
# What operators can list runs by (`Gen9RunState = "waiting"`); namespace `gen9` has it
RUN_STATE = SearchAttributeKey.for_keyword("Gen9RunState")


@workflow.defn
class RunWorkflow:
    @workflow.init
    def __init__(self, run: RunInput) -> None:
        self._run = run
        self._answered: set[str] = set()
        self._failure: str | None = (
            None  # why the last turn failed, when it can be retried
        )

    @workflow.run
    async def run(self, run: RunInput) -> str:
        status = await self._turns(run)
        # Added later: runs already in flight replay without it (docs/temporal.md,
        # "patching"). The run has succeeded either way; a run left unindexed is indexed later.
        if status == "success" and workflow.patched("index-run"):
            try:
                await workflow.execute_activity(
                    INDEX_RUN,
                    run,
                    task_queue=SYSTEM_QUEUE,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=INDEX_RETRY,
                )
            except ActivityError as err:
                workflow.logger.warning("run %s not indexed: %s", run.run_id, err.cause)
        # Added later: a background task's run tells the chat that started it how it
        # ended, in a workflow of its own that waits while that chat is busy (a cancel isn't
        # told: the agent or the person made it)
        if run.notify and status in TOLD and workflow.patched("tell-chat"):
            await workflow.start_child_workflow(
                TellChatWorkflow.run,
                TellChat(chat=run.notify, task_run=run.run_id, user_sub=run.user_sub),
                id=tell_chat_workflow_id(run.run_id),
                task_queue=SYSTEM_QUEUE,
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
        return status

    @workflow.signal(name=ANSWERED_SIGNAL)
    def answered(self, answer: AnswerInput) -> None:
        """The API checked the answer and stored it before signalling; a repeat changes nothing.
        It may arrive before this workflow has seen the turn pause, so it isn't matched against
        the pending requests here."""
        if answer.user_sub != self._run.user_sub:
            workflow.logger.warning(
                "run %s: answer from another person ignored", self._run.run_id
            )
            return
        self._answered.add(answer.input_id)

    async def _turns(self, run: RunInput) -> str:
        status, pending = await self._turn(run)
        resumed_for: set[str] = set()
        while status in ("waiting", "failed"):
            failure = self._failure if status == "failed" else None
            if failure is not None:
                # Wait for the person's Retry (a request of its own, answered like a question)
                pending = [
                    await workflow.execute_activity(
                        PARK_RUN,
                        ParkInput(run.run_id, failure),
                        task_queue=SYSTEM_QUEUE,
                        start_to_close_timeout=timedelta(seconds=30),
                        result_type=str,
                    )
                ]
            asked = set(pending)
            if asked == resumed_for:
                # The turn resumed for these answers came back without them (they weren't in
                # Postgres): wait for them to be signalled again rather than turn in a loop
                self._answered -= asked
            if not asked <= self._answered:
                workflow.upsert_search_attributes([RUN_STATE.value_set("waiting")])
                try:
                    await workflow.wait_condition(
                        lambda asked=asked: asked <= self._answered,
                        timeout=timedelta(seconds=run.wait_s)
                        if run.wait_s
                        else WAIT_FOR_PERSON,
                    )
                except TimeoutError:
                    if (
                        failure is not None
                    ):  # nobody retried: it ends as the failure it was
                        await self._finish(FinishInput(run.run_id, "error", failure))
                        return "error"
                    await self._finish(FinishInput(run.run_id, "expired"))
                    return "expired"
                except asyncio.CancelledError:
                    await self._finish(FinishInput(run.run_id, "cancelled"))
                    raise  # the workflow ends as cancelled
                workflow.upsert_search_attributes([RUN_STATE.value_set("running")])
            resumed_for = asked
            status, pending = await self._turn(run)
        return status

    async def _turn(self, run: RunInput) -> tuple[str, list[str]]:
        try:
            result: Any = await workflow.execute_activity(
                AGENT_TURN,
                run,
                task_queue=AGENT_QUEUE,
                start_to_close_timeout=timedelta(minutes=60),
                heartbeat_timeout=timedelta(seconds=3),
                retry_policy=TURN_RETRY,
                # Stop waits until the Activity has flushed what it wrote so far
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except BaseException as err:
            cancelled = is_cancelled_exception(err)
            # Added later: runs recorded before ended here whatever the failure
            if (
                not cancelled
                and _fixable_from_outside(err)
                and workflow.patched("park-failed-turn")
            ):
                self._failure = _describe(err)
                return "failed", []
            await self._finish(
                FinishInput(
                    run.run_id,
                    "cancelled" if cancelled else "error",
                    None if cancelled else _describe(err),
                )
            )
            if cancelled:
                raise  # the workflow ends as cancelled
            return "error", []
        # Turns recorded before questions returned their status alone
        if isinstance(result, str):
            return result, []
        return result["status"], list(result.get("pending") or [])

    async def _finish(self, finish: FinishInput) -> None:
        await workflow.execute_activity(
            FINISH_RUN,
            finish,
            task_queue=SYSTEM_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
        )


def _fixable_from_outside(err: BaseException) -> bool:
    """Whether a turn's failure can be fixed from outside, so the run parks for Retry: its
    retries were spent (the provider down or out of credits), it timed out, or its person is
    over their budget or their requests per minute. Failures a retry can't fix were raised
    non-retryable (runs/executor.py)."""
    cause = err.cause if isinstance(err, ActivityError) else None
    if isinstance(cause, ApplicationError):
        return not cause.non_retryable or cause.type in (BUDGET_EXCEEDED, RATE_LIMITED)
    return isinstance(cause, ActivityTimeout)


def _describe(err: BaseException) -> str:
    """The innermost cause, for the run's row (never shown to users)."""
    while err.__cause__ is not None:
        err = err.__cause__
    return f"{type(err).__name__}: {err}"[:2000]
