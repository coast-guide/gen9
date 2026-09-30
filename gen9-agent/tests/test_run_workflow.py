"""RunWorkflow: recorded histories still replay, and the workflow ends each kind of run right.

The replay test runs every history in tests/histories/ (recorded from real runs on the stacks, see
record.py) against the current workflow code: a change that would break runs already in flight,
or waiting, fails here (docs/temporal.md, rule 12). The behaviour tests run the workflow on
Temporal's time-skipping test server with fake Activities.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from gen9_agent.temporal import WORKFLOW_RUNNER
from gen9_agent.workflows.background import TellChat, TellChatWorkflow
from gen9_agent.workflows.names import (
    AGENT_QUEUE,
    AGENT_TURN,
    BUDGET_EXCEEDED,
    FINISH_RUN,
    INDEX_RUN,
    PARK_RUN,
    SYSTEM_QUEUE,
    TELL_CHAT,
)
from gen9_agent.workflows.registry import ALL_WORKFLOWS
from gen9_agent.workflows.runs import (
    RUN_STATE,
    AnswerInput,
    FinishInput,
    ParkInput,
    RunInput,
    RunWorkflow,
)

# Read at collection, outside the event loop: name -> history JSON
HISTORIES = {
    path.stem: path.read_text()
    for path in sorted(Path(__file__).with_name("histories").glob("*.json"))
}

# One event loop for the module, so the test server started once serves every test
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest.mark.parametrize("name", sorted(HISTORIES))
async def test_recorded_history_replays(name: str) -> None:
    history = WorkflowHistory.from_json(name, HISTORIES[name])
    # Every workflow the worker runs: histories of older code must replay against today's
    # (account_deleted_before_router_erase.json: a deletion from before its erase-model-usage patch)
    await Replayer(
        workflows=ALL_WORKFLOWS, workflow_runner=WORKFLOW_RUNNER
    ).replay_workflow(history)


class FakeActivities:
    """`agent_turn` runs `behaviour` (one per attempt); `finish_run` records what it was asked."""

    def __init__(self, *behaviour: str, index_fails: int = 0) -> None:
        self.behaviour = list(behaviour)
        self.attempts = 0
        self.finished: list[FinishInput] = []
        self.indexed: list[str] = []
        self.index_fails = index_fails
        self.started = asyncio.Event()
        self.paused = (
            asyncio.Event()
        )  # a turn returned "waiting", or the run was parked
        self.parked: list[ParkInput] = []
        self.go_on = asyncio.Event()  # lets a "slow-waiting" turn return

    @activity.defn(name=AGENT_TURN)
    async def agent_turn(self, run: RunInput) -> str | dict:
        self.attempts += 1
        self.started.set()
        what = self.behaviour.pop(0)
        # "waiting:a,b": the turn paused for requests a and b; "slow-waiting:a" returns that only
        # once the test lets it
        if what.startswith("slow-waiting:"):
            await self.go_on.wait()
            what = what.removeprefix("slow-")
        if what.startswith("waiting:"):
            self.paused.set()
            return {"status": "waiting", "pending": what.split(":", 1)[1].split(",")}
        if what == "transient":
            raise RuntimeError("connection reset")
        if what == "budget":
            raise ApplicationError(
                "over budget", type=BUDGET_EXCEEDED, non_retryable=True
            )
        if what == "permanent":
            raise ApplicationError(
                "NotFoundError: no such model", type="NotFoundError", non_retryable=True
            )
        if what == "until-cancelled":
            while True:
                activity.heartbeat()
                await asyncio.sleep(0.1)
        return "success"

    @activity.defn(name=FINISH_RUN)
    async def finish_run(self, finish: FinishInput) -> None:
        self.finished.append(finish)

    @activity.defn(name=PARK_RUN)
    async def park_run(self, park: ParkInput) -> str:
        self.parked.append(park)
        self.paused.set()
        return f"retry-{len(self.parked)}"

    @activity.defn(name=INDEX_RUN)
    async def index_run(self, run: RunInput) -> None:
        if self.index_fails:
            self.index_fails -= 1
            raise RuntimeError("model router unreachable")
        self.indexed.append(run.run_id)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        # As gen9-temporal's namespace has it (scripts/setup-namespace.sh): a waiting run sets it
        await env.client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=env.client.namespace,
                search_attributes={
                    "Gen9RunState": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                },
            )
        )
        yield env


@asynccontextmanager
async def running(env: WorkflowEnvironment, fake: FakeActivities, run: RunInput):
    """The workflow started with both workers up; yields its handle."""
    async with (
        Worker(env.client, task_queue=AGENT_QUEUE, activities=[fake.agent_turn]),
        Worker(
            env.client,
            task_queue=SYSTEM_QUEUE,
            workflows=[RunWorkflow],
            workflow_runner=WORKFLOW_RUNNER,
            activities=[fake.finish_run, fake.park_run, fake.index_run],
        ),
    ):
        yield await env.client.start_workflow(
            RunWorkflow.run, run, id=f"run-{run.run_id}", task_queue=SYSTEM_QUEUE
        )


def a_run(wait_s: int | None = None) -> RunInput:
    return RunInput(run_id=str(uuid.uuid4()), user_sub="sub-1", wait_s=wait_s)


async def until_waiting(handle) -> None:
    """Until the workflow itself waits for the person (it sets Gen9RunState), not merely until
    the turn returned. On the time-skipping test server, a cancel that races the paused turn's
    completion can leave the workflow task unfinished; a real server ended 40 of 40 such races
    as `cancelled` (explore/hitl/NOTES.md)."""
    for _ in range(200):
        described = await handle.describe()
        if described.typed_search_attributes.get(RUN_STATE) == "waiting":
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the workflow never waited")


async def answered(handle, input_id: str, user_sub: str = "sub-1") -> None:
    await handle.signal("answered", AnswerInput(input_id=input_id, user_sub=user_sub))


async def run_workflow(env: WorkflowEnvironment, fake: FakeActivities, cancel=False):
    run = RunInput(run_id=str(uuid.uuid4()), user_sub="sub-1")
    async with (
        Worker(
            env.client,
            task_queue=AGENT_QUEUE,
            activities=[fake.agent_turn],
        ),
        Worker(
            env.client,
            task_queue=SYSTEM_QUEUE,
            workflows=[RunWorkflow],
            workflow_runner=WORKFLOW_RUNNER,
            activities=[fake.finish_run, fake.park_run, fake.index_run],
        ),
    ):
        handle = await env.client.start_workflow(
            RunWorkflow.run, run, id=f"run-{run.run_id}", task_queue=SYSTEM_QUEUE
        )
        if cancel:
            await fake.started.wait()
            await handle.cancel()
        return run, await handle.result()


async def test_success_needs_no_finish_and_is_indexed(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("success")
    run, result = await run_workflow(env, fake)
    assert result == "success"
    assert fake.attempts == 1 and fake.finished == []
    assert fake.indexed == [run.run_id]


async def test_indexing_is_retried_and_never_fails_the_run(
    env: WorkflowEnvironment,
) -> None:
    fake = FakeActivities("success", index_fails=3)
    run, result = await run_workflow(env, fake)
    assert result == "success" and fake.indexed == [run.run_id]
    gone = FakeActivities("success", index_fails=100)  # the router stays down
    _, result = await run_workflow(env, gone)
    assert result == "success" and gone.indexed == []


async def test_transient_failures_are_retried(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("transient", "transient", "success")
    _, result = await run_workflow(env, fake)
    assert result == "success"
    assert fake.attempts == 3 and fake.finished == []


async def test_permanent_failure_ends_the_run_at_once(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("permanent", "success")
    run, result = await run_workflow(env, fake)
    assert result == "error"
    assert fake.attempts == 1 and fake.indexed == []
    [finish] = fake.finished
    assert (finish.run_id, finish.status) == (run.run_id, "error")
    assert finish.error and "no such model" in finish.error


async def test_retries_run_out_and_nobody_retries(env: WorkflowEnvironment) -> None:
    """Spent retries park the run for Retry; with none before its wait ends, it ends as error."""
    fake = FakeActivities("transient", "transient", "transient")
    run = a_run(wait_s=3600)
    async with running(env, fake, run) as handle:
        assert await handle.result() == "error"
    assert fake.attempts == 3
    [parked] = fake.parked
    assert "connection reset" in parked.error
    [finish] = fake.finished
    assert (finish.status, finish.error) == ("error", parked.error)


async def test_stop_cancels_the_turn_and_records_it(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("until-cancelled")
    with pytest.raises(WorkflowFailureError) as failure:
        await run_workflow(env, fake, cancel=True)
    assert isinstance(failure.value.cause, CancelledError)
    assert [f.status for f in fake.finished] == ["cancelled"]


# Questions mid-task: the workflow waits for an `answered` Signal per request (workflows/runs.py)


async def test_waits_for_every_answer_then_resumes(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("waiting:a,b", "success")
    async with running(env, fake, a_run()) as handle:
        await fake.paused.wait()
        await answered(handle, "a")
        await env.sleep(timedelta(days=1))
        assert fake.attempts == 1  # still waiting for b
        await answered(handle, "b")
        assert await handle.result() == "success"
    assert fake.attempts == 2 and fake.finished == []


async def test_no_answer_in_time_expires(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("waiting:a")
    run = a_run(wait_s=3600)
    async with running(env, fake, run) as handle:
        assert await handle.result() == "expired"
    assert [(f.run_id, f.status) for f in fake.finished] == [(run.run_id, "expired")]
    assert fake.indexed == []


async def test_the_default_wait_is_seven_days(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("waiting:a", "success")
    async with running(env, fake, a_run()) as handle:
        await fake.paused.wait()
        await env.sleep(timedelta(days=6, hours=23))
        await answered(handle, "a")
        assert await handle.result() == "success"


async def test_stop_while_waiting_cancels(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("waiting:a")
    async with running(env, fake, a_run()) as handle:
        await until_waiting(handle)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as failure:
            await handle.result()
    assert isinstance(failure.value.cause, CancelledError)
    assert [f.status for f in fake.finished] == ["cancelled"]


async def test_an_answer_from_another_person_is_ignored(
    env: WorkflowEnvironment,
) -> None:
    fake = FakeActivities("waiting:a", "success")
    async with running(env, fake, a_run(wait_s=3600)) as handle:
        await fake.paused.wait()
        await answered(handle, "a", user_sub="someone-else")
        assert await handle.result() == "expired"
    assert fake.attempts == 1


async def test_an_answer_before_the_pause_is_seen_counts(
    env: WorkflowEnvironment,
) -> None:
    fake = FakeActivities("slow-waiting:a", "success")
    async with running(env, fake, a_run(wait_s=3600)) as handle:
        await fake.started.wait()
        await answered(handle, "a")  # the turn hasn't returned yet
        fake.go_on.set()
        assert await handle.result() == "success"
    assert fake.attempts == 2


async def test_a_resume_without_the_answers_waits_again(
    env: WorkflowEnvironment,
) -> None:
    """The resumed turn found no stored answer: the workflow waits for it to be signalled again,
    rather than turning in a loop."""
    fake = FakeActivities("waiting:a", "waiting:a", "success")
    async with running(env, fake, a_run()) as handle:
        await fake.paused.wait()
        fake.paused.clear()
        await answered(handle, "a")
        await fake.paused.wait()  # the second turn paused again for a
        await env.sleep(timedelta(days=1))
        assert fake.attempts == 2
        await answered(handle, "a")
        assert await handle.result() == "success"
    assert fake.attempts == 3


# Retry: a turn that failed in a way someone can fix parks the run (workflows/runs.py)


async def test_a_parked_run_goes_on_when_the_person_retries(
    env: WorkflowEnvironment,
) -> None:
    fake = FakeActivities("transient", "transient", "transient", "success")
    async with running(env, fake, a_run()) as handle:
        await fake.paused.wait()
        await env.sleep(timedelta(days=1))
        assert fake.attempts == 3  # nothing runs until Retry
        await answered(handle, "retry-1")
        assert await handle.result() == "success"
    assert fake.attempts == 4 and fake.finished == []


async def test_a_run_over_budget_parks_too(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("budget", "success")
    async with running(env, fake, a_run()) as handle:
        await fake.paused.wait()
        assert fake.attempts == 1  # not retried by Temporal: the limit resets later
        await answered(handle, "retry-1")
        assert await handle.result() == "success"
    [parked] = fake.parked
    assert "over budget" in parked.error


async def test_a_retry_that_fails_again_parks_again(env: WorkflowEnvironment) -> None:
    fake = FakeActivities("budget", "budget", "success")
    async with running(env, fake, a_run()) as handle:
        await fake.paused.wait()
        fake.paused.clear()
        await answered(handle, "retry-1")
        await fake.paused.wait()
        await answered(handle, "retry-2")
        assert await handle.result() == "success"
    assert len(fake.parked) == 2 and fake.attempts == 3


async def test_a_background_tasks_run_tells_its_chat_when_it_ends(
    env: WorkflowEnvironment,
) -> None:
    """A task's run (background.py) starts the notice's workflow as it ends; a chat's own run
    starts none."""
    told: list[TellChat] = []

    @activity.defn(name=TELL_CHAT)
    async def tell_chat(tell: TellChat) -> str:
        told.append(tell)
        return "sent"

    for notify in ("chat-1", None):
        fake = FakeActivities("success")
        run = RunInput(run_id=str(uuid.uuid4()), user_sub="sub-1", notify=notify)
        async with (
            Worker(env.client, task_queue=AGENT_QUEUE, activities=[fake.agent_turn]),
            Worker(
                env.client,
                task_queue=SYSTEM_QUEUE,
                workflows=[RunWorkflow, TellChatWorkflow],
                workflow_runner=WORKFLOW_RUNNER,
                activities=[fake.finish_run, fake.park_run, fake.index_run, tell_chat],
            ),
        ):
            result = await env.client.execute_workflow(
                RunWorkflow.run, run, id=f"run-{run.run_id}", task_queue=SYSTEM_QUEUE
            )
            assert result == "success"
            if notify:
                tell = env.client.get_workflow_handle(f"tell-chat-{run.run_id}")
                assert await tell.result() == "sent"
    assert told == [
        TellChat(chat="chat-1", task_run=told[0].task_run, user_sub="sub-1")
    ]
