"""A background task's notice (background.py): when a task's run ends, its chat gets a turn of its
own that says so, as Claude Code's completion notifications and Deep Agents' completion callback
do. `RunWorkflow` starts this workflow; it outlives the task's run (`ABANDON`)."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .names import SYSTEM_QUEUE, TELL_CHAT

# While the chat has a run going, the notice waits: first 10 s, doubling up to 5 min, for a day
FIRST_WAIT = timedelta(seconds=10)
LONGEST_WAIT = timedelta(minutes=5)
TELL_FOR = timedelta(days=1)


@dataclass(frozen=True)
class TellChat:
    chat: str  # the chat that started the task
    task_run: str  # the task's run that ended
    user_sub: str


@workflow.defn
class TellChatWorkflow:
    @workflow.run
    async def run(self, tell: TellChat) -> str:
        """sent (or sent before), gone (the chat or task was deleted), or busy (a day passed)."""
        deadline = workflow.now() + TELL_FOR
        wait = FIRST_WAIT
        while True:
            said = await workflow.execute_activity(
                TELL_CHAT,
                tell,
                task_queue=SYSTEM_QUEUE,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if said != "busy" or workflow.now() + wait > deadline:
                return said
            await workflow.sleep(wait)
            wait = min(wait * 2, LONGEST_WAIT)
