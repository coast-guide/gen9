"""Deleting a chat, an account, and the data of users deleted in Keycloak directly.

Each deletion is a workflow of idempotent Activities, so a step that fails (Langfuse unreachable,
say) is retried by Temporal until it succeeds instead of failing the request, and a repeated
request joins the running deletion (workflow ids `delete-thread-<id>`, `delete-account-<sub>`).
The order never leaves personal data behind an account that no longer exists. Design:
docs/plans/harness.md, "how deletion moves onto Temporal".
"""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.workflow import ParentClosePolicy

from .names import (
    DELETE_KEYCLOAK_USER,
    DELETE_RUN_HISTORIES,
    DELETE_THREAD_DATA,
    DELETE_USER_DATA,
    DISABLE_KEYCLOAK_USER,
    ERASE_MODEL_USAGE,
    ERASE_TRACES,
    FIND_DELETED_USERS,
    REMOVE_THREAD_ENVIRONMENT,
    REMOVE_USER_ENVIRONMENTS,
    REMOVE_USER_TASKS,
    REVOKE_CONNECTOR_TOKENS,
    STOP_THREAD_RUNS,
    SYSTEM_QUEUE,
    delete_account_workflow_id,
)


@dataclass(frozen=True)
class DeleteThreadInput:
    thread_id: str
    since: str  # ISO timestamp: the thread's traces can't be older (its creation)


@dataclass(frozen=True)
class DeleteAccountInput:
    sub: str
    since: str  # ISO timestamp: the user's traces can't be older (their first visit)
    keycloak: bool = True  # False for users already deleted in Keycloak (the sweep)


@dataclass(frozen=True)
class EraseTraces:
    since: str
    session: str | None = None  # a chat: its thread id
    user: str | None = None  # an account: its sub


@dataclass(frozen=True)
class RunHistories:
    thread: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class DeletedUser:
    sub: str
    since: str


@dataclass(frozen=True)
class Step:
    """How one kind of step is timed and retried."""

    start_to_close: timedelta
    retry: RetryPolicy
    heartbeat: timedelta | None = None
    # Never give up: a deletion that failed partway would leave the person's data behind an API
    # that had answered 202 (M9, F22). Temporal's default: no limit on time or attempts; each
    # attempt is still bounded by `start_to_close`, and retries add no history events.
    schedule_to_close: timedelta | None = None


# Quick steps: a few attempts cover a restarting service
QUICK = Step(
    start_to_close=timedelta(minutes=2),
    retry=RetryPolicy(maximum_interval=timedelta(seconds=30)),
)
# Steps that wait on another service (Langfuse may be down for hours): back off to 5 minutes
PATIENT = Step(
    start_to_close=timedelta(minutes=5),
    heartbeat=timedelta(minutes=1),
    retry=RetryPolicy(
        initial_interval=timedelta(seconds=5), maximum_interval=timedelta(minutes=5)
    ),
)


async def _step(name: str, arg: object, step: Step) -> None:
    await workflow.execute_activity(
        name,
        arg,
        task_queue=SYSTEM_QUEUE,
        start_to_close_timeout=step.start_to_close,
        heartbeat_timeout=step.heartbeat,
        retry_policy=step.retry,
        schedule_to_close_timeout=step.schedule_to_close,
    )


# Langfuse ingests traces a few seconds after a run (0.8-6 s measured on an idle stack, longer
# under load), so a chat deleted right after an answer may have a trace that isn't there yet when
# the first erasure runs. Erase again after these durable timers.
LATE_ERASE_PASSES = (timedelta(minutes=1), timedelta(minutes=10))


class _EarlyReturn:
    """The `deleted` Update returns once the person's data is gone, while the workflow goes on
    with the late trace erasures (Temporal's early-return pattern): the API answers 204 then."""

    def __init__(self) -> None:
        self._deleted = False

    @workflow.update
    async def deleted(self) -> None:
        await workflow.wait_condition(lambda: self._deleted)

    async def _late_erasures(
        self, traces: EraseTraces, model_usage_of: str | None = None
    ) -> None:
        """`model_usage_of`: a user whose router records to erase again too, as the router writes
        its daily totals in batches."""
        for delay in LATE_ERASE_PASSES:
            await workflow.sleep(delay)
            await _step(ERASE_TRACES, traces, PATIENT)
            if model_usage_of is not None:
                await _step(ERASE_MODEL_USAGE, model_usage_of, PATIENT)


@workflow.defn
class DeleteThreadWorkflow(_EarlyReturn):
    """1. stop the chat's running answer, then remove its environment; 2. its Langfuse traces;
    3. its runs' and environment's history in Temporal;
    4. its checkpoints and row (runs and events cascade); then its traces again, twice, for any
    that arrived late."""

    @workflow.run
    async def run(self, chat: DeleteThreadInput) -> None:
        traces = EraseTraces(since=chat.since, session=chat.thread_id)
        await _step(STOP_THREAD_RUNS, chat.thread_id, PATIENT)
        # Added later: the chat's environment (its sandbox) goes as soon as nothing runs in
        # it; deletions already in flight replay without it
        if workflow.patched("remove-environments"):
            await _step(REMOVE_THREAD_ENVIRONMENT, chat.thread_id, PATIENT)
        await _step(ERASE_TRACES, traces, PATIENT)
        await _step(DELETE_RUN_HISTORIES, RunHistories(thread=chat.thread_id), PATIENT)
        await _step(DELETE_THREAD_DATA, chat.thread_id, QUICK)
        self._deleted = True
        await self._late_erasures(traces)


@workflow.defn
class DeleteAccountWorkflow(_EarlyReturn):
    """1. disable the Keycloak user and end its sessions (nobody signs in meanwhile); 2. Langfuse
    traces; then its connectors' tokens revoked at their servers (RFC 7009, best effort) and its
    environments removed;
    3. every chat, its checkpoints, and the user row; 4. the model router's records of the
    user (usage and cost; gen9-models' admin API); 5. the runs' history in Temporal; 6. the
    Keycloak user; then traces and router records again, twice, for any that arrived late (they
    need only the sub).
    Without `keycloak` (a user deleted in Keycloak directly), steps 1 and 6 are skipped."""

    @workflow.run
    async def run(self, account: DeleteAccountInput) -> None:
        traces = EraseTraces(since=account.since, user=account.sub)
        if account.keycloak:
            await _step(DISABLE_KEYCLOAK_USER, account.sub, QUICK)
        await _step(ERASE_TRACES, traces, PATIENT)
        # Added later: the connectors' tokens revoked at their servers before the
        # connectors go (RFC 7009); deletions already in flight replay without it
        if workflow.patched("revoke-connector-tokens"):
            await _step(REVOKE_CONNECTOR_TOKENS, account.sub, PATIENT)
        # Added later: every environment of the account (its sandboxes)
        if workflow.patched("remove-environments"):
            await _step(REMOVE_USER_ENVIRONMENTS, account.sub, PATIENT)
        # Added later: its scheduled tasks stop firing (their Schedules removed)
        if workflow.patched("remove-tasks"):
            await _step(REMOVE_USER_TASKS, account.sub, PATIENT)
        await _step(DELETE_USER_DATA, account.sub, PATIENT)
        # Added later: deletions already in flight replay without it (docs/temporal.md,
        # "patching"); the answer is memoized, so the late passes below follow it too
        erase_model_usage = workflow.patched("erase-model-usage")
        if erase_model_usage:
            await _step(ERASE_MODEL_USAGE, account.sub, PATIENT)
        await _step(DELETE_RUN_HISTORIES, RunHistories(user=account.sub), PATIENT)
        if account.keycloak:
            await _step(DELETE_KEYCLOAK_USER, account.sub, QUICK)
        self._deleted = True
        # Added later: the late passes for users deleted in Keycloak too, whose answer still
        # running when step 3 stopped it lands its trace after the first pass (M9, F24)
        if account.keycloak or workflow.patched("late-erasures-always"):
            await self._late_erasures(
                traces, account.sub if erase_model_usage else None
            )


@workflow.defn
class SweepDeletedUsersWorkflow:
    """Started by the `sweep-deleted-users` Schedule (and the admin API): removes Gen9's data of
    users who no longer exist in Keycloak, each as its own DeleteAccountWorkflow, started and left
    to finish on its own (ParentClosePolicy.ABANDON). Returns how many deletions it started."""

    @workflow.run
    async def run(self) -> int:
        users = await workflow.execute_activity(
            FIND_DELETED_USERS,
            task_queue=SYSTEM_QUEUE,
            result_type=list[DeletedUser],
            start_to_close_timeout=QUICK.start_to_close,
            retry_policy=QUICK.retry,
        )
        # Added later: each deletion started and left to run (its late passes take ten
        # minutes), not awaited one after another; sweeps already in flight replay awaiting (F24)
        leave = workflow.patched("sweep-starts-deletions")
        started = 0
        for user in users:
            deletion = DeleteAccountInput(
                sub=user.sub, since=user.since, keycloak=False
            )
            try:
                if leave:
                    await workflow.start_child_workflow(
                        DeleteAccountWorkflow.run,
                        deletion,
                        id=delete_account_workflow_id(user.sub),
                        task_queue=SYSTEM_QUEUE,
                        parent_close_policy=ParentClosePolicy.ABANDON,
                    )
                else:
                    await workflow.execute_child_workflow(
                        DeleteAccountWorkflow.run,
                        deletion,
                        id=delete_account_workflow_id(user.sub),
                        task_queue=SYSTEM_QUEUE,
                    )
                started += 1
            except WorkflowAlreadyStartedError:
                pass  # already being deleted (the account was deleted from Gen9 meanwhile)
        return started
