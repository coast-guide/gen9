"""Text that must say something (not_blank.py; gen9-learn.md, M9, F8): a message, a task's name
and its prompt of only spaces are refused in words a person reads, and a blank first message
can't fail the end of a run."""

import pytest
from pydantic import ValidationError

from gen9_agent.api.tasks import ScheduleIn, TaskIn, TaskPatch
from gen9_agent.api.threads import RunIn
from gen9_agent.runs.store import chat_title

pytestmark = pytest.mark.asyncio

SCHEDULE = ScheduleIn(kind="daily", time="09:00")


def refusal(error: pytest.ExceptionInfo[ValidationError]) -> list[str]:
    return [e["msg"] for e in error.value.errors()]


async def test_a_message_of_spaces_is_refused_and_one_with_words_kept_as_sent() -> None:
    for blank in (" ", "   \n  ", "\t"):
        with pytest.raises(ValidationError) as refused:
            RunIn(message=blank)
        assert refusal(refused) == ["Value error, Write a message first."]
    assert RunIn(message="  indented code\n").message == "  indented code\n"
    with pytest.raises(ValidationError):
        RunIn(message="x" * 8001)


async def test_a_task_needs_a_name_and_something_to_do() -> None:
    with pytest.raises(ValidationError) as refused:
        TaskIn(name="  ", prompt=" \n", schedule=SCHEDULE, time_zone="Europe/London")
    assert refusal(refused) == [
        "Value error, Name the task.",
        "Value error, Say what Gen9 should do.",
    ]
    kept = TaskIn(name="Brief", prompt="Summarise", schedule=SCHEDULE, time_zone="UTC")
    assert (kept.name, kept.prompt) == ("Brief", "Summarise")


async def test_a_change_may_leave_them_out_but_not_blank_them() -> None:
    assert TaskPatch().name is None and TaskPatch(name="Renamed").name == "Renamed"
    with pytest.raises(ValidationError) as refused:
        TaskPatch(name=" ")
    assert refusal(refused) == ["Value error, Name the task."]


async def test_a_chats_title_is_its_first_line_and_never_fails() -> None:
    assert chat_title("  What is RFC 10017?\nAnd why?") == "What is RFC 10017?"
    assert chat_title("x" * 80) == "x" * 80
    assert chat_title("x" * 100) == "x" * 79 + "…"
    # Cut at a word, its comma dropped, within 80
    long = "Search the web: what is the latest released version of Valkey? One sentence, with its source."
    assert (
        chat_title(long)
        == "Search the web: what is the latest released version of Valkey? One sentence…"
    )
    assert len(chat_title(long)) <= 80
    assert chat_title("   \n  ") == "New chat"


class Recorded:
    """An engine whose one connection records the statements a transaction runs."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, statement):
        self.statements.append(str(statement))
        return "11111111-1111-1111-1111-111111111111"

    async def execute(self, statement, *args):
        self.statements.append(
            str(statement.compile(compile_kwargs={"literal_binds": True}))
        )


async def test_a_chat_is_named_when_its_first_run_is_queued(monkeypatch) -> None:
    # Not when its answer succeeds: one waiting for Allow left "New chat · Needs you" (M9, F21)
    import uuid

    from gen9_agent.runs import store

    async def append(*args, **kwargs) -> int:
        return 1

    monkeypatch.setattr(store.log, "append", append)
    engine = Recorded()
    await store.enqueue(engine, uuid.uuid4(), {"message": "Compare plans\nin detail"})  # ty: ignore[invalid-argument-type]
    named = [s for s in engine.statements if "UPDATE threads SET title" in s]
    assert len(named) == 1 and "'Compare plans'" in named[0]
    # Only a chat still called "New chat": a rename or a task's name stays
    assert "threads.title = 'New chat'" in named[0]
