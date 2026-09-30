"""The Activity of a background task's notice (background.py, workflows/background.py): a run in
the chat that started the task, saying how it ended."""

import uuid

from sqlalchemy import exists, select
from temporalio import activity
from temporalio.client import Client

from . import background
from .models import Run, Thread
from .runs import control, store
from .runtime import Runtime
from .workflows.background import TellChat
from .workflows.names import PRIORITY_BACKGROUND, TELL_CHAT


class BackgroundActivities:
    def __init__(self, runtime: Runtime, temporal: Client) -> None:
        self.runtime = runtime
        self.temporal = temporal

    @activity.defn(name=TELL_CHAT)
    async def tell_chat(self, tell: TellChat) -> str:
        """sent (now, or by an earlier attempt), gone, or busy while the chat has a run going."""
        engine = self.runtime.engine
        chat, task_run = uuid.UUID(tell.chat), uuid.UUID(tell.task_run)
        async with engine.connect() as conn:
            mode = await conn.scalar(
                select(Thread.permission_mode).where(
                    Thread.id == chat, Thread.deleted_at.is_(None)
                )
            )
            task = (
                await conn.execute(
                    select(Thread.id, Thread.title, Run.status)
                    .join(Run, Run.thread_id == Thread.id)
                    .where(Run.id == task_run, Thread.deleted_at.is_(None))
                )
            ).first()
            if mode is None or task is None:
                return "gone"
            # Marked with the task's run, so a repeated attempt never tells it twice
            told = await conn.scalar(
                select(
                    exists().where(
                        Run.thread_id == chat,
                        Run.input["notice_of"].astext == tell.task_run,
                    )
                )
            )
        if told:
            return "sent"
        answer = (
            await background.answer_of(self.runtime.agent, task.id)
            if task.status == "success"
            else ""
        )
        try:
            await control.start_run(
                engine,
                self.temporal,
                chat,
                tell.user_sub,
                {
                    "message": background.notice(
                        str(task.id), task.title, task.status, answer
                    ),
                    "permission_mode": mode,
                    "notice_of": tell.task_run,
                },
                priority=PRIORITY_BACKGROUND,
            )
        except store.ActiveRunExists:
            return "busy"
        return "sent"

    def all(self) -> list:
        return [self.tell_chat]
