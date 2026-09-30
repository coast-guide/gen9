"""The operator's stop (stop.py): every run cancelled, every running task Schedule paused, and
`--resume` unpausing only what a stop paused (P5-C10)."""

from types import SimpleNamespace

import pytest

from gen9_agent import stop

pytestmark = pytest.mark.asyncio


class Schedules:
    """Temporal's Schedules by id: whether each is paused, and its note."""

    def __init__(self, states: dict[str, tuple[bool, str]]) -> None:
        self.states = states

    async def list_schedules(self):
        async def listed():
            for schedule_id in self.states:
                yield SimpleNamespace(id=schedule_id)

        return listed()

    def get_schedule_handle(self, schedule_id: str):
        states = self.states

        class Handle:
            async def describe(self):
                paused, note = states[schedule_id]
                return SimpleNamespace(
                    schedule=SimpleNamespace(
                        state=SimpleNamespace(paused=paused, note=note)
                    )
                )

            async def pause(self, note: str) -> None:
                states[schedule_id] = (True, note)

            async def unpause(self, note: str) -> None:
                states[schedule_id] = (False, note)

        return Handle()


async def test_a_stop_cancels_every_run_and_pauses_running_tasks_only(
    monkeypatch,
) -> None:
    stopped, recorded = [], []

    async def stop_runs_of(engine, temporal, user_sub, wait_s=0):
        stopped.append((user_sub, wait_s))
        return 3

    async def record(engine, action, detail):
        recorded.append((action, detail))

    monkeypatch.setattr(stop.control, "stop_runs_of", stop_runs_of)
    monkeypatch.setattr(stop, "_record", record)
    temporal = Schedules(
        {
            "task-a": (False, ""),
            "task-b": (True, "paused by its person"),
            "sweep-deleted-users": (False, ""),
        }
    )
    done = await stop.stop(None, temporal)  # ty: ignore[invalid-argument-type]
    # Everyone's runs, waited for; the running task paused; its person's pause, and Gen9's own
    # sweep, left as they were
    assert stopped == [(None, 60)] and done == {"runs": 3, "schedules": 1}
    assert temporal.states == {
        "task-a": (True, stop.NOTE),
        "task-b": (True, "paused by its person"),
        "sweep-deleted-users": (False, ""),
    }
    assert recorded == [("operator.stop", {"runs": 3, "schedules": 1})]

    assert await stop.resume(None, temporal) == {"schedules": 1}  # ty: ignore[invalid-argument-type]
    assert temporal.states["task-a"][0] is False
    assert temporal.states["task-b"] == (True, "paused by its person")
    assert recorded[-1] == ("operator.resume", {"schedules": 1})
