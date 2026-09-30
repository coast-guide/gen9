"""The turn's Activity (runs/activities.py): what it runs in the chat's environment goes with its
heartbeat, and an attempt retried after a crashed worker stops what the last one left running
before the turn resumes (manual-e2e.md, P3-C6)."""

import asyncio
import dataclasses
import uuid
from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from gen9_agent import environments
from gen9_agent.runs import activities
from gen9_agent.workflows.runs import RunInput

pytestmark = pytest.mark.asyncio

LEFT = {"exec-1": {"sandbox": "sandbox-1", "command": "./loop.sh"}}


async def turn(monkeypatch, attempt: int, heartbeat: list) -> tuple[list, list]:
    """Runs agent_turn as attempt `attempt`, the last attempt's heartbeat details `heartbeat`;
    returns what happened in order, and the heartbeats it sent."""
    happened: list = []
    beats: list = []

    async def stop_left(settings, left):
        happened.append(("stopped", left))
        return [where["command"] for where in left.values()]

    async def execute(runtime, run_id, attempt):
        # A command starts: the turn's record has it, and the next heartbeat carries it
        environments.RUNNING.get()["exec-2"] = {"sandbox": "sandbox-1", "command": "ls"}
        await asyncio.sleep(activities.HEARTBEAT_S * 1.5)
        happened.append(("ran", attempt, environments.STOPPED.get()))
        return {"status": "success"}

    monkeypatch.setattr(activities, "stop_left", stop_left)
    monkeypatch.setattr(activities, "execute", execute)
    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        env.info, attempt=attempt, heartbeat_details=heartbeat
    )
    env.on_heartbeat = lambda *details: beats.append(details)
    runtime = SimpleNamespace(settings=None, environments=object())
    await env.run(
        activities.RunActivities(runtime).agent_turn,  # ty: ignore[invalid-argument-type]
        RunInput(run_id=str(uuid.uuid4()), user_sub="sub-1"),
    )
    return happened, beats


async def test_a_retry_stops_what_the_crashed_attempt_left_before_it_runs(
    monkeypatch,
) -> None:
    happened, beats = await turn(monkeypatch, 2, [LEFT])
    assert happened == [("stopped", LEFT), ("ran", 2, {"./loop.sh"})]
    assert ({"exec-2": {"sandbox": "sandbox-1", "command": "ls"}},) in beats


async def test_a_first_attempt_has_nothing_to_stop(monkeypatch) -> None:
    happened, _ = await turn(monkeypatch, 1, [])
    assert happened == [("ran", 1, None)]
