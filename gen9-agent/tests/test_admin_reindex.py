"""`POST /v1/admin/search/reindex` starts ReindexSearchWorkflow once, on the system queue, and
returns at once. The admin check is stood in for here: the live check showed a non-admin gets 403,
and the CLI's tokens carry no roles to reach the success path from a script (plan, Surprises)."""

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from temporalio.common import WorkflowIDConflictPolicy

from gen9_agent.api import admin
from gen9_agent.auth import Principal
from gen9_agent.workflows.names import (
    PRIORITY_MAINTENANCE,
    REINDEX_NOW_WORKFLOW_ID,
    SYSTEM_QUEUE,
)
from gen9_agent.workflows.search import ReindexInput, ReindexSearchWorkflow

pytestmark = pytest.mark.asyncio


@dataclass
class Handle:
    id: str
    result_run_id: str


class FakeTemporal:
    def __init__(self) -> None:
        self.started: list[tuple] = []

    async def start_workflow(self, workflow, arg, **options) -> Handle:
        self.started.append((workflow, arg, options))
        return Handle(options["id"], "run-1")


async def test_starts_one_reindex_and_returns_at_once() -> None:
    app = FastAPI()
    app.include_router(admin.router)
    app.state.temporal = temporal = FakeTemporal()
    # The dependency behind AdminPrincipal (require_role("gen9-admin"))
    requires_admin = admin.AdminPrincipal.__metadata__[0].dependency
    app.dependency_overrides[requires_admin] = lambda: Principal(
        sub="ada", client_id="gen9-ui", roles=frozenset({"gen9-admin"})
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.post("/v1/admin/search/reindex")
    assert response.status_code == 202
    assert response.json() == {
        "workflow_id": REINDEX_NOW_WORKFLOW_ID,
        "run_id": "run-1",
    }
    [(workflow, arg, options)] = temporal.started
    assert workflow == ReindexSearchWorkflow.run and arg == ReindexInput()
    assert options["id"] == REINDEX_NOW_WORKFLOW_ID
    assert options["task_queue"] == SYSTEM_QUEUE
    # Asked again while it runs: the same workflow, not a second one
    assert options["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
    assert options["priority"].priority_key == PRIORITY_MAINTENANCE
