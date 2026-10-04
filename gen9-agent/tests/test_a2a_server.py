"""Gen9 as an A2A agent (a2a_server.py): its Agent Card, the endpoint refusing a call without a
token, and a state for every run status. Tasks on the real stacks are e2e's (`e2e/a2a.mjs`)."""

import httpx
import pytest
from fastapi import FastAPI

from gen9_agent import a2a_server
from gen9_agent.models import ACTIVE_RUN_STATUSES, FINAL_RUN_STATUSES
from gen9_agent.settings import Settings

pytestmark = pytest.mark.asyncio

SETTINGS = Settings.model_construct(
    keycloak_issuer="http://localhost:15000/realms/gen9",
    keycloak_internal_url="http://gen9-keycloak:8080",
    gen9_api_public_url="http://localhost:17000",
    a2a_wait_s=120,
    run_wait_s=3600,
)


async def _client() -> httpx.AsyncClient:
    app = FastAPI()
    a2a_server.mount(app, SETTINGS)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:17000"
    )


async def test_the_agent_card_says_where_and_how_to_sign_in() -> None:
    async with await _client() as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
    assert card["name"] == "Gen9"
    assert card["supportedInterfaces"] == [
        {
            "url": "http://localhost:17000/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert card["capabilities"] == {"streaming": True, "pushNotifications": False}
    flow = card["securitySchemes"]["keycloak"]["oauth2SecurityScheme"]["flows"][
        "authorizationCode"
    ]
    assert flow["authorizationUrl"] == (
        "http://localhost:15000/realms/gen9/protocol/openid-connect/auth"
    )
    assert flow["scopes"] == {"gen9-a2a": "Send Gen9 tasks and read their results"}
    assert flow["pkceRequired"] is True


async def test_a_call_without_a_token_is_refused() -> None:
    async with await _client() as http:
        bare = await http.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "ListTasks", "params": {}},
        )
    assert bare.status_code == 401
    assert bare.headers["www-authenticate"] == 'Bearer scope="gen9-a2a"'


async def test_every_run_status_has_its_state() -> None:
    assert set(a2a_server.STATE) == set(ACTIVE_RUN_STATUSES) | set(FINAL_RUN_STATUSES)


async def test_a_client_reaches_only_the_chats_it_started() -> None:
    # P5-C7: its consent says "send it tasks and read their results"
    import uuid

    from sqlalchemy import and_
    from sqlalchemy.dialects import postgresql

    from gen9_agent.a2a_server import Caller
    from gen9_agent.models import User

    caller = Caller(User(id=uuid.uuid4(), sub="alan"), "an-agent")
    sql = str(
        and_(*caller.chats()).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "threads.a2a_client = 'an-agent'" in sql
    assert "threads.user_id = " in sql and "threads.deleted_at IS NULL" in sql


async def test_a_run_gone_since_its_owner_was_checked_is_not_found() -> None:
    """Its chat deleted meanwhile: `TaskNotFoundError`, not an assertion's 500 (gen9-learn.md,
    M9, F17)."""
    import uuid
    from types import SimpleNamespace

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, model, key):
            return None

    handler = a2a_server.Gen9.__new__(a2a_server.Gen9)
    handler.app = SimpleNamespace(state=SimpleNamespace(sessionmaker=Session))  # ty: ignore[invalid-assignment]
    with pytest.raises(a2a_server.TaskNotFoundError):
        await handler.task(uuid.uuid4())


async def test_the_agent_card_gives_gen9s_own_version() -> None:
    """One version everywhere (deploy.md, U7): the card says what the API and the CLI say."""
    from importlib.metadata import version
    from types import SimpleNamespace

    from gen9_agent.a2a_server import card

    settings = SimpleNamespace(
        keycloak_issuer="http://keycloak/realms/gen9", gen9_a2a_url="http://api/a2a"
    )
    assert card(settings).version == version("gen9-agent")  # ty: ignore[invalid-argument-type]
