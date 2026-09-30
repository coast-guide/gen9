"""A scheduled task's firing (tasks.py): its Schedule, or a delayed start for a one-off, starts
this workflow. An Activity makes the chat and its run in Postgres; then the chat's usual
`RunWorkflow` runs as its child, at background priority with the person as fairness key, and the
firing waits for it. So the Schedule's overlap Skip skips the next firing while this one's run
goes on or waits for the person (Decision Log).

A task with a rubric (outcomes.py) is graded after each run that succeeds; on "needs revision",
another run in the same chat takes the grader's findings as its message, until the rubric is met
or the task's tries are used up. Then one notice tells the person."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import Priority, RetryPolicy, SearchAttributeKey
from temporalio.exceptions import ActivityError, ChildWorkflowError
from temporalio.workflow import ParentClosePolicy

from .names import (
    CONTINUE_TASK,
    FIRE_TASK,
    GRADE_RUN,
    NOTIFY_OUTCOME,
    PRIORITY_BACKGROUND,
    SYSTEM_QUEUE,
    run_workflow_id,
)
from .runs import RunInput, RunWorkflow

# Set by Temporal on a workflow its Schedule started: the action's time (docs: Schedule)
SCHEDULED_AT = SearchAttributeKey.for_datetime("TemporalScheduledStartTime")


@dataclass(frozen=True)
class TaskFiring:
    task_id: str
    user_sub: str
    # Text sent with an API trigger (api/tasks.py): data for the run, not instructions. Added
    # later; firings recorded before have none
    text: str | None = None


@dataclass(frozen=True)
class FiredRun:
    run_id: str  # empty: the task is gone
    thread_id: str


@dataclass(frozen=True)
class Graded:
    """A grading Activity's verdict on a run (outcomes.py): whether the task has a rubric at all,
    the result, how many runs the firing may take, and the next run's message if it revises."""

    graded: bool
    result: str = ""
    tries: int = 0
    message: str = ""


@workflow.defn
class TaskFiringWorkflow:
    @workflow.run
    async def run(self, firing: TaskFiring) -> str | None:
        """The run it started, or None when the task is gone."""
        args: list[str | None] = [firing.task_id, firing.text]
        # Added later: a firing from the Schedule says when it was due, so a local time
        # that comes round twice (the clocks going back) runs once (tasks.py, "Daylight saving")
        if workflow.patched("scheduled-time"):
            scheduled = workflow.info().typed_search_attributes.get(SCHEDULED_AT)
            if scheduled is not None:
                args.append(scheduled.isoformat())
        fired = await workflow.execute_activity(
            FIRE_TASK,
            args=args,
            task_queue=SYSTEM_QUEUE,
            result_type=FiredRun,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        if not fired.run_id:  # the task is gone
            return None
        status = await self._run(fired.run_id, firing.user_sub)
        # Added later: a task with a rubric is graded after each run, and revised until
        # it meets it or runs out of tries (outcomes.py)
        if not workflow.patched("outcomes"):
            return fired.run_id
        run_id, iteration = fired.run_id, 0
        while status == "success":
            try:
                graded = await workflow.execute_activity(
                    GRADE_RUN,
                    args=[run_id, iteration],
                    task_queue=SYSTEM_QUEUE,
                    result_type=Graded,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError as err:
                # The grader couldn't: the run is done, ungraded, and its person told so
                workflow.logger.warning("run %s not graded: %s", run_id, err.cause)
                graded = Graded(graded=True, result="ungraded")
            if not graded.graded:
                break
            if graded.result == "needs_revision" and iteration + 1 < graded.tries:
                run_id = await workflow.execute_activity(
                    CONTINUE_TASK,
                    args=[fired.thread_id, graded.message],
                    task_queue=SYSTEM_QUEUE,
                    result_type=str,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
                if not run_id:  # its person took the chat over
                    break
                iteration += 1
                status = await self._run(run_id, firing.user_sub)
                continue
            final = (
                "max_iterations_reached"
                if graded.result == "needs_revision"
                else graded.result
            )
            await workflow.execute_activity(
                NOTIFY_OUTCOME,
                args=[run_id, final],
                task_queue=SYSTEM_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            break
        return fired.run_id

    async def _run(self, run_id: str, user_sub: str) -> str:
        """The chat's run, as a child: how it ended."""
        try:
            return await workflow.execute_child_workflow(
                RunWorkflow.run,
                RunInput(run_id=run_id, user_sub=user_sub),
                id=run_workflow_id(run_id),
                task_queue=SYSTEM_QUEUE,
                # Deleting or changing the task ends the firing, never the run
                parent_close_policy=ParentClosePolicy.ABANDON,
                priority=Priority(
                    priority_key=PRIORITY_BACKGROUND, fairness_key=user_sub
                ),
            )
        except ChildWorkflowError:
            # Stopped by its person, or failed: the run's own state is in Postgres, and the
            # firing is over either way
            return "error"
