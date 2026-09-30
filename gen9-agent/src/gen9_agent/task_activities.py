"""The Activities of scheduled tasks (tasks.py, workflows/tasks.py): a firing's chat and run, and
removing a person's Schedules when their account goes."""

import uuid
from datetime import datetime

from sqlalchemy import select
from temporalio import activity
from temporalio.client import Client

from . import notices, outcomes, tasks
from .models import Task, User
from .runtime import Runtime
from .workflows.names import (
    CONTINUE_TASK,
    FIRE_TASK,
    GRADE_RUN,
    NOTIFY_OUTCOME,
    REMOVE_USER_TASKS,
)
from .workflows.tasks import FiredRun, Graded


class TaskActivities:
    def __init__(self, runtime: Runtime, temporal: Client) -> None:
        self.runtime = runtime
        self.temporal = temporal

    @activity.defn(name=FIRE_TASK)
    async def fire_task(
        self, task_id: str, text: str | None = None, scheduled: str | None = None
    ) -> FiredRun:
        """The firing's new chat and its run; empty when the task is gone, or when its local time
        came round a second time (`scheduled`, ISO 8601, from its Schedule)."""
        return await tasks.fire(
            self.runtime,
            uuid.UUID(task_id),
            text,
            datetime.fromisoformat(scheduled) if scheduled else None,
        )

    @activity.defn(name=GRADE_RUN)
    async def grade_run(self, run_id: str, iteration: int) -> Graded:
        """The run graded against its task's rubric (outcomes.py)."""
        return await outcomes.grade(self.runtime, uuid.UUID(run_id), iteration)

    @activity.defn(name=CONTINUE_TASK)
    async def continue_task(self, thread_id: str, message: str) -> str:
        """The next try's run in the chat; empty when its person took the chat over."""
        return await outcomes.revise(self.runtime, uuid.UUID(thread_id), message)

    @activity.defn(name=NOTIFY_OUTCOME)
    async def notify_outcome(self, run_id: str, result: str) -> None:
        """The person's one notice about a graded firing: done, or didn't meet its rubric."""
        await notices.notify_safely(
            self.runtime,
            uuid.UUID(run_id),
            "done" if result in ("satisfied", "ungraded") else "unmet",
            graded=True,
        )

    @activity.defn(name=REMOVE_USER_TASKS)
    async def remove_user_tasks(self, sub: str) -> int:
        """Every Schedule (and delayed start) of the person's tasks; the rows go with their
        data. Repeating it removes nothing more."""
        async with self.runtime.engine.connect() as conn:
            ids = list(
                (
                    await conn.execute(
                        select(Task.id)
                        .join(User, User.id == Task.user_id)
                        .where(User.sub == sub)
                    )
                ).scalars()
            )
        for task_id in ids:
            await tasks.unarrange(self.temporal, task_id)
        return len(ids)

    def all(self) -> list:
        return [
            self.fire_task,
            self.grade_run,
            self.continue_task,
            self.notify_outcome,
            self.remove_user_tasks,
        ]
