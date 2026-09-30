"""Deletion workflows: the order of their steps, retries until a failing step succeeds, and the
sweep running one account deletion per missing user. Temporal's time-skipping test server, fake
Activities."""

import asyncio
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from gen9_agent.temporal import WORKFLOW_RUNNER
from gen9_agent.workflows.deletion import (
    DeleteAccountInput,
    DeleteAccountWorkflow,
    DeletedUser,
    DeleteThreadInput,
    DeleteThreadWorkflow,
    EraseTraces,
    RunHistories,
    SweepDeletedUsersWorkflow,
)
from gen9_agent.workflows.names import (
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

pytestmark = pytest.mark.asyncio(loop_scope="module")
SINCE = "2026-09-01T00:00:00+00:00"


class Fakes:
    """Records each step; ERASE_TRACES fails `langfuse_down` times first (Langfuse unreachable),
    each time asking to be retried after `retry_after` (the Retry Policy's own interval if None)."""

    def __init__(
        self,
        langfuse_down: int = 0,
        missing: tuple[str, ...] = (),
        retry_after: timedelta | None = None,
    ) -> None:
        self.steps: list[str] = []
        self.langfuse_down = langfuse_down
        self.missing = missing
        self.retry_after = retry_after

    def all(self) -> list:
        steps = self.steps

        @activity.defn(name=STOP_THREAD_RUNS)
        async def stop_thread_runs(thread_id: str) -> None:
            steps.append("stop runs")

        @activity.defn(name=ERASE_TRACES)
        async def erase_traces(request: EraseTraces) -> int:
            if self.langfuse_down:
                self.langfuse_down -= 1
                if self.retry_after:
                    raise ApplicationError(
                        "langfuse down", next_retry_delay=self.retry_after
                    )
                raise ConnectionError("langfuse down")
            steps.append(f"traces {request.session or request.user}")
            return 1

        @activity.defn(name=DELETE_RUN_HISTORIES)
        async def delete_run_histories(request: RunHistories) -> int:
            steps.append(f"histories {request.thread or request.user}")
            return 0

        @activity.defn(name=DELETE_THREAD_DATA)
        async def delete_thread_data(thread_id: str) -> None:
            steps.append("thread data")

        @activity.defn(name=DISABLE_KEYCLOAK_USER)
        async def disable_keycloak_user(sub: str) -> None:
            steps.append(f"disable {sub}")

        @activity.defn(name=REMOVE_THREAD_ENVIRONMENT)
        async def remove_thread_environment(thread_id: str) -> int:
            steps.append("environment")
            return 0

        @activity.defn(name=REMOVE_USER_ENVIRONMENTS)
        async def remove_user_environments(sub: str) -> int:
            steps.append(f"environments {sub}")
            return 0

        @activity.defn(name=REMOVE_USER_TASKS)
        async def remove_user_tasks(sub: str) -> int:
            steps.append(f"tasks {sub}")
            return 0

        @activity.defn(name=REVOKE_CONNECTOR_TOKENS)
        async def revoke_connector_tokens(sub: str) -> int:
            steps.append(f"revoke {sub}")
            return 0

        @activity.defn(name=DELETE_USER_DATA)
        async def delete_user_data(sub: str) -> None:
            steps.append(f"data {sub}")

        @activity.defn(name=ERASE_MODEL_USAGE)
        async def erase_model_usage(sub: str) -> dict:
            steps.append(f"model usage {sub}")
            return {}

        @activity.defn(name=DELETE_KEYCLOAK_USER)
        async def delete_keycloak_user(sub: str) -> None:
            steps.append(f"keycloak {sub}")

        @activity.defn(name=FIND_DELETED_USERS)
        async def find_deleted_users() -> list[DeletedUser]:
            return [DeletedUser(sub=s, since=SINCE) for s in self.missing]

        return [
            stop_thread_runs,
            erase_traces,
            delete_run_histories,
            delete_thread_data,
            disable_keycloak_user,
            revoke_connector_tokens,
            remove_thread_environment,
            remove_user_environments,
            remove_user_tasks,
            delete_user_data,
            erase_model_usage,
            delete_keycloak_user,
            find_deleted_users,
        ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def run(env: WorkflowEnvironment, fakes: Fakes, workflow, arg=None):
    async with Worker(
        env.client,
        task_queue=SYSTEM_QUEUE,
        workflows=[
            DeleteThreadWorkflow,
            DeleteAccountWorkflow,
            SweepDeletedUsersWorkflow,
        ],
        workflow_runner=WORKFLOW_RUNNER,
        activities=fakes.all(),
    ):
        args = [] if arg is None else [arg]
        return await env.client.execute_workflow(
            workflow, *args, id=f"test-{uuid.uuid4()}", task_queue=SYSTEM_QUEUE
        )


CHAT_STEPS = [
    "stop runs",
    "environment",  # the chat's sandbox, once nothing runs in it
    "traces t1",
    "histories t1",
    "thread data",
    "traces t1",  # late passes, after 1 and 10 minutes, for traces ingested late
    "traces t1",
]


async def test_a_chat_is_deleted_in_order(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    await run(env, fakes, DeleteThreadWorkflow.run, DeleteThreadInput("t1", SINCE))
    assert fakes.steps == CHAT_STEPS


async def test_deleted_returns_once_the_data_is_gone(env: WorkflowEnvironment) -> None:
    """The API waits on this Update (204) while the late erasures run on."""
    fakes = Fakes()
    async with Worker(
        env.client,
        task_queue=SYSTEM_QUEUE,
        workflows=[DeleteThreadWorkflow],
        workflow_runner=WORKFLOW_RUNNER,
        activities=fakes.all(),
    ):
        handle = await env.client.start_workflow(
            DeleteThreadWorkflow.run,
            DeleteThreadInput("t1", SINCE),
            id=f"test-{uuid.uuid4()}",
            task_queue=SYSTEM_QUEUE,
        )
        await handle.execute_update(DeleteThreadWorkflow.deleted)
        assert fakes.steps == CHAT_STEPS[:5]
        await handle.result()
    assert fakes.steps == CHAT_STEPS


async def test_langfuse_down_is_waited_out_and_nothing_is_deleted_before(
    env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(langfuse_down=5)  # backoff runs on the skipped clock
    await run(env, fakes, DeleteThreadWorkflow.run, DeleteThreadInput("t1", SINCE))
    assert fakes.steps == CHAT_STEPS
    assert fakes.langfuse_down == 0


async def test_an_account_deletion_outlasts_an_outage_of_over_a_week(
    env: WorkflowEnvironment,
) -> None:
    """A step retries until it succeeds, however long its service is down: a deletion that gave
    up would leave the person's chats and row behind an API that had answered 202 (M9, F22)."""
    fakes = Fakes(langfuse_down=9, retry_after=timedelta(days=1))
    await run(env, fakes, DeleteAccountWorkflow.run, DeleteAccountInput("u1", SINCE))
    assert fakes.langfuse_down == 0
    assert "data u1" in fakes.steps and "keycloak u1" in fakes.steps


async def test_an_account_is_disabled_first_and_removed_from_keycloak_after_its_data(
    env: WorkflowEnvironment,
) -> None:
    fakes = Fakes()
    await run(env, fakes, DeleteAccountWorkflow.run, DeleteAccountInput("u1", SINCE))
    assert fakes.steps == [
        "disable u1",
        "traces u1",
        "revoke u1",  # connectors' tokens, before the connectors go
        "environments u1",
        "tasks u1",  # their Schedules, before the rows go
        "data u1",
        "model usage u1",
        "histories u1",
        "keycloak u1",
        "traces u1",  # late passes, by sub
        "model usage u1",
        "traces u1",
        "model usage u1",
    ]


async def test_the_sweep_starts_each_missing_users_deletion_and_leaves_it_running(
    env: WorkflowEnvironment,
) -> None:
    """The sweep starts a deletion per user without Keycloak's steps and ends at once; each goes on
    by itself (ParentClosePolicy.ABANDON), rather than being awaited ten minutes apiece (M9, F24).
    Their late passes are the next test's: the time-skipping server hangs when time is skipped
    across a timer then an Activity in an abandoned child (a probe)."""
    fakes = Fakes(missing=("gone1", "gone2"))
    async with Worker(
        env.client,
        task_queue=SYSTEM_QUEUE,
        workflows=[SweepDeletedUsersWorkflow, DeleteAccountWorkflow],
        workflow_runner=WORKFLOW_RUNNER,
        activities=fakes.all(),
    ):
        started = await env.client.execute_workflow(
            SweepDeletedUsersWorkflow.run,
            id=f"test-{uuid.uuid4()}",
            task_queue=SYSTEM_QUEUE,
        )
        assert started == 2
        for _ in range(100):  # their first steps, in real time
            if {"histories gone1", "histories gone2"} <= set(fakes.steps):
                break
            await asyncio.sleep(0.05)
        for sub in ("gone1", "gone2"):
            handle = env.client.get_workflow_handle(delete_account_workflow_id(sub))
            assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
            assert [step for step in fakes.steps if step.endswith(f" {sub}")] == [
                f"traces {sub}",
                f"revoke {sub}",
                f"environments {sub}",
                f"tasks {sub}",
                f"data {sub}",
                f"model usage {sub}",
                f"histories {sub}",
            ]
            await handle.terminate()  # its late passes would outlive the Worker


async def test_an_account_gone_from_keycloak_is_erased_again_late_too(
    env: WorkflowEnvironment,
) -> None:
    """A user deleted in Keycloak directly: no Keycloak steps, and the late passes still run, as an
    answer its step 3 stopped lands its trace after the first erasure (M9, F24)."""
    fakes = Fakes()
    await run(
        env,
        fakes,
        DeleteAccountWorkflow.run,
        DeleteAccountInput("gone", SINCE, keycloak=False),
    )
    assert fakes.steps == [
        "traces gone",
        "revoke gone",
        "environments gone",
        "tasks gone",
        "data gone",
        "model usage gone",
        "histories gone",
        "traces gone",  # late passes, after 1 and 10 minutes
        "model usage gone",
        "traces gone",
        "model usage gone",
    ]
