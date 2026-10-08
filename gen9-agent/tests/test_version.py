"""Which Gen9 runs, for someone signed in (health.py, `/v1/version`): this build's version, the
same across Gen9's packages and a release's tag, and the commit its image was built from
(docs/plans/deploy.md, U7). Not to anyone else: a version names the vulnerabilities to try."""

from importlib.metadata import version
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from gen9_agent.api import health
from gen9_agent.auth import Principal, get_principal

pytestmark = pytest.mark.asyncio


async def call(signed_in: bool, commit: str = "") -> httpx.Response:
    app = FastAPI()
    app.include_router(health.router)
    app.state.runtime = SimpleNamespace(settings=SimpleNamespace(gen9_commit=commit))
    if signed_in:
        app.dependency_overrides[get_principal] = lambda: Principal(
            sub="alan", client_id="gen9-ui", roles=frozenset({"gen9-user"})
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        return await client.get("/v1/version")


async def test_someone_signed_in_sees_the_version_and_commit() -> None:
    response = await call(True, commit="0123abc")
    assert response.status_code == 200
    assert response.json() == {"version": version("gen9-agent"), "commit": "0123abc"}


async def test_a_build_made_here_has_no_commit() -> None:
    assert (await call(True)).json()["commit"] == ""


async def test_nobody_else_sees_it() -> None:
    assert (await call(False)).status_code == 401
