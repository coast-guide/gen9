import asyncio
from types import SimpleNamespace
from typing import Self

import pytest
from temporalio.service import RPCError, RPCStatusCode

from gen9_agent import environment_activities
from gen9_agent.environment_activities import EnvironmentActivities
from gen9_agent.workflows.environment import CheckEnvironment
from gen9_agent.workflows.names import END_SIGNAL


def activities(monkeypatch, handle: object, steps: list[str]) -> EnvironmentActivities:
    async def of_thread(settings: object, thread_id: str) -> list[str]:
        steps.append("listed")
        return ["left-over"]

    async def remove(settings: object, sandbox_ids: list[str]) -> int:
        steps.append(f"removed {','.join(sandbox_ids)}")
        return len(sandbox_ids)

    monkeypatch.setattr(environment_activities.environments, "of_thread", of_thread)
    monkeypatch.setattr(environment_activities.environments, "remove", remove)
    runtime = SimpleNamespace(settings=SimpleNamespace(sandbox_url="http://sandbox"))
    temporal = SimpleNamespace(get_workflow_handle=lambda workflow_id: handle)
    return EnvironmentActivities(runtime, temporal)  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_a_deleted_chats_workflow_removes_its_sandbox_before_the_sweep(
    monkeypatch,
) -> None:
    """The sweep waits for the environment workflow's own removal: both at once raced, and
    Docker refused the second (P3-C13)."""
    steps: list[str] = []

    class Handle:
        async def signal(self, name: str) -> None:
            steps.append(name)

        async def result(self) -> None:
            await asyncio.sleep(0.05)
            steps.append("workflow removed its sandbox")

    removed = await activities(monkeypatch, Handle(), steps).remove_thread_environment(
        "thread-1"
    )

    assert steps == [
        END_SIGNAL,
        "workflow removed its sandbox",
        "listed",
        "removed left-over",
    ]
    assert removed == 1


@pytest.mark.asyncio
async def test_with_no_workflow_or_a_slow_one_the_sweep_still_runs(monkeypatch) -> None:
    steps: list[str] = []

    class Gone:
        async def signal(self, name: str) -> None:
            raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")

    await activities(monkeypatch, Gone(), steps).remove_thread_environment("thread-1")
    assert steps == ["listed", "removed left-over"]

    steps.clear()
    monkeypatch.setattr(environment_activities, "ENDING_S", 0.05)

    class Slow:
        async def signal(self, name: str) -> None:
            steps.append(name)

        async def result(self) -> None:
            await asyncio.sleep(10)

    await activities(monkeypatch, Slow(), steps).remove_thread_environment("thread-1")
    assert steps == [END_SIGNAL, "listed", "removed left-over"]


class _Conn:
    def __init__(self, thread: str | None) -> None:
        self.thread = thread

    async def scalar(self, statement: object) -> str | None:
        return self.thread

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Standing:
    def __init__(self, active: bool) -> None:
        self.is_active = active
        self.asked: list[tuple[str, bool]] = []

    async def active(self, sub: str, fresh: bool = False) -> bool:
        self.asked.append((sub, fresh))
        return self.is_active


def _creator(monkeypatch, thread_alive: bool, person_active: bool):
    removed: list[list[str]] = []

    async def secrets_of(engine: object, vault: object, sub: str) -> list:
        return []

    async def create(
        settings: object, thread_id: str, sub: str, timeout_s: int, secrets: list
    ) -> str:
        return "sandbox-new"

    async def remove(settings: object, ids: list[str]) -> int:
        removed.append(ids)
        return len(ids)

    monkeypatch.setattr(environment_activities.environments, "secrets_of", secrets_of)
    monkeypatch.setattr(environment_activities.environments, "create", create)
    monkeypatch.setattr(environment_activities.environments, "remove", remove)
    standing = _Standing(person_active)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(),
        vault=None,
        engine=SimpleNamespace(
            connect=lambda: _Conn("thread-1" if thread_alive else None)
        ),
        standing=standing,
    )
    return EnvironmentActivities(runtime, SimpleNamespace()), removed, standing  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_a_sandbox_made_for_a_live_chat_stays(monkeypatch) -> None:
    from gen9_agent.workflows.environment import CreateEnvironment

    activities, removed, standing = _creator(
        monkeypatch, thread_alive=True, person_active=True
    )
    assert (
        await activities.create_environment(
            CreateEnvironment("thread-1", "sub-1", 3600)
        )
        == "sandbox-new"
    )
    assert removed == [] and standing.asked == [("sub-1", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thread_alive", "person_active"), [(False, True), (True, False)]
)
async def test_a_sandbox_made_while_its_chat_or_account_was_deleted_goes_at_once(
    monkeypatch, thread_alive: bool, person_active: bool
) -> None:
    """The deletion swept before it existed, and took its workflow: nothing else would remove it
    for an hour (P3-Z1)."""
    from temporalio.exceptions import ApplicationError

    from gen9_agent.workflows.environment import CreateEnvironment

    activities, removed, _ = _creator(monkeypatch, thread_alive, person_active)
    with pytest.raises(ApplicationError) as refused:
        await activities.create_environment(
            CreateEnvironment("thread-1", "sub-1", 3600)
        )
    assert refused.value.non_retryable
    assert removed == [["sandbox-new"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("drift", "rewritten", "steps_after"),
    [
        (False, False, ["names read", "compared"]),
        (True, True, ["names read", "compared", "secrets opened", "rewritten"]),
        # OpenSandbox or the sidecar doesn't answer: the command goes on, and finds out itself
        (RuntimeError("proxy: connection refused"), False, ["names read", "compared"]),
    ],
)
async def test_an_environment_whose_secrets_drifted_is_rewritten(
    monkeypatch, drift, rewritten, steps_after
) -> None:
    """Its egress sidecar restarted and lost its vault (P4-E4): the next acquire puts the person's
    secrets back, opening them only then."""
    steps: list[str] = []

    async def secret_hosts_of(engine: object, user_sub: str) -> dict[str, str]:
        steps.append("names read")
        return {"key": "key.example.com"}

    async def drifted(
        settings: object, sandbox_id: str, secrets: dict[str, str]
    ) -> bool:
        steps.append("compared")
        if isinstance(drift, Exception):
            raise drift
        return drift

    async def secrets_of(engine: object, vault: object, user_sub: str) -> list[str]:
        steps.append("secrets opened")
        return ["opened"]

    async def apply_secrets(
        settings: object, sandbox_id: str, secrets: list[str]
    ) -> None:
        assert (sandbox_id, secrets) == ("sb-1", ["opened"])
        steps.append("rewritten")

    envs = environment_activities.environments
    for name, fn in [
        ("secret_hosts_of", secret_hosts_of),
        ("drifted", drifted),
        ("secrets_of", secrets_of),
        ("apply_secrets", apply_secrets),
    ]:
        monkeypatch.setattr(envs, name, fn)
    runtime = SimpleNamespace(settings=SimpleNamespace(), engine=None, vault=None)
    acts = EnvironmentActivities(runtime, SimpleNamespace())  # ty: ignore[invalid-argument-type]
    assert (
        await acts.check_environment_secrets(CheckEnvironment("sb-1", "sub-1"))
        is rewritten
    )
    assert steps == steps_after
