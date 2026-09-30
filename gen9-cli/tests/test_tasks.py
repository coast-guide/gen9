"""`gen9 tasks` (main.py): what it sends and how it lists tasks."""

import argparse

import pytest

from gen9_cli.main import format_tasks, local_zone, schedule_of, when

pytestmark = pytest.mark.asyncio


async def test_every_becomes_the_apis_schedule() -> None:
    def args(**kw: object) -> argparse.Namespace:
        return argparse.Namespace(**{"at": "09:00", "on": None, **kw})

    assert schedule_of(args(every="weekday")) == {"kind": "weekdays", "time": "09:00"}
    assert schedule_of(args(every="week", on="sun")) == {
        "kind": "weekly",
        "time": "09:00",
        "weekday": 6,
    }
    assert schedule_of(args(every="once", on="2026-10-01", at="07:30")) == {
        "kind": "once",
        "time": "07:30",
        "date": "2026-10-01",
    }


async def test_the_list_says_when_each_runs() -> None:
    assert "Nothing scheduled" in format_tasks([])
    text = format_tasks(
        [
            {
                "name": "Morning brief",
                "status": "paused",
                "schedule_words": "Every weekday at 09:00 (UTC)",
                "next_at": None,
                "runs": [
                    {
                        "thread_id": "t1",
                        "status": "success",
                        "created_at": "2026-09-25T09:00:42Z",
                    }
                ],
            }
        ]
    )
    assert text.splitlines() == [
        "Morning brief (paused)",
        "  Every weekday at 09:00 (UTC)",
        "  last: 2026-09-25 09:00 success, chat t1",
    ]


async def test_a_task_with_a_rubric_says_how_its_last_run_did() -> None:
    text = format_tasks(
        [
            {
                "name": "Weekly report",
                "status": "active",
                "schedule_words": "Every Monday at 08:00 (UTC)",
                "rubric": "- Every item has a date",
                "max_iterations": 3,
                "runs": [
                    {
                        "thread_id": "t2",
                        "status": "success",
                        "created_at": "2026-09-28T08:00:12Z",
                        "outcome": "satisfied",
                        "graded": 2,
                        "checking": False,
                    }
                ],
            }
        ]
    )
    assert "checked against a rubric, 3 tries at most" in text
    assert "success, meets its rubric in 2 tries, chat t2" in text


async def test_the_terminals_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", ":Asia/Kolkata")
    assert local_zone() == "Asia/Kolkata"


async def test_times_are_the_terminals_wall_clock_to_the_minute() -> None:
    assert when("2026-09-27T03:30:12Z", "Asia/Kolkata") == "2026-09-27 09:00"
    assert when("2026-09-26T14:59:34.093321Z", "UTC") == "2026-09-26 14:59"
    # An unknown zone falls back to UTC rather than failing the list
    assert when("2026-09-27T03:30:12Z", "Nowhere/Else") == "2026-09-27 03:30"
    one = format_tasks(
        [
            {
                "name": "Daily",
                "status": "active",
                "schedule_words": "Every day at 09:00 (Asia/Kolkata)",
                "next_at": "2026-09-27T03:30:12Z",
                "rubric": "- ok",
                "max_iterations": 2,
                "runs": [
                    {
                        "thread_id": "t3",
                        "status": "success",
                        "created_at": "2026-09-26T14:50:14Z",
                        "outcome": "failed",
                        "graded": 1,
                        "checking": False,
                    }
                ],
            }
        ],
        "Asia/Kolkata",
    )
    assert "  next: 2026-09-27 09:00" in one
    assert "in 1 try, chat t3" in one
