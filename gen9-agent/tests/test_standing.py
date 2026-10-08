"""Whether work may still be done for a person (standing.py): Keycloak says, once a minute."""

import httpx
import pytest

from gen9_agent.keycloak_admin import KeycloakAdminError
from gen9_agent.runs.store import PUBLIC_NO_ANSWER, PUBLIC_NO_IDENTITY, retry_reason
from gen9_agent.standing import KEYCLOAK_UNAVAILABLE, IdentityUnavailable, Standing

pytestmark = pytest.mark.asyncio


class Admin:
    """Keycloak's answers by user id: a user, or an HTTP status it fails with."""

    def __init__(self, users: dict[str, dict | int]) -> None:
        self.users = users
        self.asked: list[str] = []

    async def get_user(self, user_id: str) -> dict:
        self.asked.append(user_id)
        answer = self.users[user_id]
        if isinstance(answer, int):
            raise KeycloakAdminError(answer, "Keycloak said no")
        return answer


def standing(users: dict[str, dict | int], ttl_s: float = 60) -> tuple[Standing, Admin]:
    admin = Admin(users)
    return Standing(admin, ttl_s), admin  # ty: ignore[invalid-argument-type]


async def test_enabled_is_active_disabled_and_deleted_are_not() -> None:
    s, _ = standing({"ada": {"enabled": True}, "bob": {"enabled": False}, "gone": 404})
    assert await s.active("ada")
    assert not await s.active("bob")
    assert not await s.active("gone")


async def test_keycloak_is_asked_once_a_minute_per_person() -> None:
    s, admin = standing({"ada": {"enabled": True}})
    for _ in range(3):
        assert await s.active("ada")
    assert admin.asked == ["ada"]
    fresh, admin = standing({"ada": {"enabled": True}}, ttl_s=0)
    await fresh.active("ada")
    await fresh.active("ada")
    assert admin.asked == ["ada", "ada"]


async def test_a_keycloak_that_fails_raises_rather_than_guessing() -> None:
    s, _ = standing({"ada": 503})
    with pytest.raises(IdentityUnavailable, match=KEYCLOAK_UNAVAILABLE):
        await s.active("ada")


class Down(Admin):
    async def get_user(self, user_id: str) -> dict:
        raise httpx.ConnectError("[Errno -2] Name or service not known")


async def test_a_keycloak_that_is_down_raises_and_the_run_says_so() -> None:
    with pytest.raises(IdentityUnavailable) as raised:
        await Standing(Down({})).active("ada")  # ty: ignore[invalid-argument-type]
    # As the run's failure carries it (workflows/runs.py, `_describe`), then its Retry's words:
    # the sign-in service, not the model provider (manual-e2e.md, P8-N3)
    failure = f"ApplicationError: {raised.value}"
    assert retry_reason(failure) == PUBLIC_NO_IDENTITY
    assert (
        retry_reason("ApplicationError: APIConnectionError: timed out")
        == PUBLIC_NO_ANSWER
    )


async def test_without_an_admin_client_it_cant_tell_and_says_yes() -> None:
    assert await Standing().active("anyone")


# P5-C10: a person disabled mid-turn stops the turn, in a subagent too


class DisabledLater(Admin):
    """Enabled for Keycloak's first `until` answers, disabled after: an admin acting mid-turn."""

    def __init__(self, until: int) -> None:
        super().__init__({"alan": {"enabled": True}})
        self.until = until

    async def get_user(self, user_id: str) -> dict:
        self.asked.append(user_id)
        return {"enabled": len(self.asked) <= self.until}


async def test_a_turn_stops_once_its_person_is_disabled_even_inside_a_subagent() -> (
    None
):
    import uuid

    from deepagents import create_deep_agent
    from langgraph.checkpoint.memory import InMemorySaver

    from gen9_agent.memory import Gen9Context
    from gen9_agent.standing import PersonInactive, StillActive
    from tests.test_grounding import Delegating, Looping, look_up

    # Asked before each model call (no minute kept here): the agent's first, then the subagent's
    admin = DisabledLater(until=4)
    guard = StillActive(Standing(admin, ttl_s=0))  # ty: ignore[invalid-argument-type]
    main, helper = Delegating(), Looping()
    agent = create_deep_agent(
        model=main,
        tools=[look_up],
        checkpointer=InMemorySaver(),
        subagents=[
            {
                "name": "general-purpose",
                "description": "Helps.",
                "system_prompt": "Help.",
                "model": helper,
                "tools": [look_up],
                "middleware": [guard],
            }
        ],
        middleware=[guard],
        context_schema=Gen9Context,
    )
    with pytest.raises(PersonInactive):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "find everything"}]},
            {"configurable": {"thread_id": str(uuid.uuid4()), "recursion_limit": 500}},
            context=Gen9Context("alan"),
        )
    # It stopped in the subagent at the first call after the disable, not at any budget
    assert (main.calls, helper.calls) == (1, 3)
