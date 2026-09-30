"""ReindexSearchWorkflow on Temporal's time-skipping test server with fake Activities, and the
index helpers it relies on (search_index.py)."""

import uuid

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from gen9_agent.search_index import (
    check_model,
    distance_sql,
    index_name,
    vector_type,
)
from gen9_agent.temporal import WORKFLOW_RUNNER
from gen9_agent.workflows.names import (
    CURRENT_EMBED_MODEL,
    DROP_STALE_SEARCH_INDEXES,
    ENSURE_SEARCH_INDEX,
    REINDEX_BATCH,
    SYSTEM_QUEUE,
)
from gen9_agent.workflows.search import (
    BatchResult,
    EmbedModel,
    ReindexBatch,
    ReindexInput,
    ReindexResult,
    ReindexSearchWorkflow,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")

MODEL = EmbedModel("ollama/embeddinggemma", 768)


class FakeSearch:
    """Runs `a`..`e` need work; batches of 2 by id. `down` batch attempts fail first."""

    def __init__(self, down: int = 0) -> None:
        self.runs = ["a", "b", "c", "d", "e"]
        self.afters: list[str | None] = []
        self.ensured: list[EmbedModel] = []
        self.dropped_for: list[str] = []
        self.down = down

    @activity.defn(name=CURRENT_EMBED_MODEL)
    async def current_embed_model(self) -> EmbedModel:
        return MODEL

    @activity.defn(name=ENSURE_SEARCH_INDEX)
    async def ensure_search_index(self, current: EmbedModel) -> str | None:
        self.ensured.append(current)
        return index_name(current.model)

    @activity.defn(name=REINDEX_BATCH)
    async def reindex_batch(self, batch: ReindexBatch) -> BatchResult:
        if self.down:
            self.down -= 1
            raise RuntimeError("model router unreachable")
        self.afters.append(batch.after)
        todo = [r for r in self.runs if batch.after is None or r > batch.after][:2]
        failed = 1 if "c" in todo else 0  # one chat whose text the provider refuses
        return BatchResult(
            embedded=len(todo) - failed,
            failed=failed,
            last=todo[-1] if len(todo) == 2 else None,
        )

    @activity.defn(name=DROP_STALE_SEARCH_INDEXES)
    async def drop_stale_search_indexes(self, model: str) -> list[str]:
        self.dropped_for.append(model)
        return ["chat_search_hnsw_old"]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def reindex(
    env: WorkflowEnvironment, fake: FakeSearch, state: ReindexInput
) -> ReindexResult:
    async with Worker(
        env.client,
        task_queue=SYSTEM_QUEUE,
        workflows=[ReindexSearchWorkflow],
        workflow_runner=WORKFLOW_RUNNER,
        activities=[
            fake.current_embed_model,
            fake.ensure_search_index,
            fake.reindex_batch,
            fake.drop_stale_search_indexes,
        ],
    ):
        return await env.client.execute_workflow(
            ReindexSearchWorkflow.run,
            state,
            id=f"reindex-{uuid.uuid4()}",
            task_queue=SYSTEM_QUEUE,
        )


async def test_pages_through_every_run_then_drops_old_indexes(
    env: WorkflowEnvironment,
) -> None:
    fake = FakeSearch()
    result = await reindex(env, fake, ReindexInput())
    assert fake.ensured == [MODEL]
    assert fake.afters == [None, "b", "d"]
    assert result == ReindexResult(
        MODEL.model, embedded=4, failed=1, dropped=["chat_search_hnsw_old"]
    )
    assert fake.dropped_for == [MODEL.model]


async def test_a_router_outage_retries_the_batch_without_losing_its_place(
    env: WorkflowEnvironment,
) -> None:
    fake = FakeSearch(down=3)
    result = await reindex(env, fake, ReindexInput())
    assert fake.afters == [None, "b", "d"]
    assert (result.embedded, result.failed) == (4, 1)


async def test_continues_from_the_state_it_was_given(env: WorkflowEnvironment) -> None:
    # What continue-as-new carries: the cursor and the counts so far
    fake = FakeSearch()
    result = await reindex(env, fake, ReindexInput(after="b", embedded=2, failed=0))
    assert fake.afters == ["b", "d"]
    assert (result.embedded, result.failed) == (4, 1)


async def test_index_names_types_and_distance_follow_the_model() -> None:
    small = "openai/text-embedding-3-small"
    assert index_name(small) == index_name(small)
    assert index_name(small) != index_name("ollama/embeddinggemma")
    assert (
        index_name(small).startswith("chat_search_hnsw_")
        and len(index_name(small)) == 29
    )
    assert check_model("ollama/embeddinggemma:latest")
    for bad in ["a'b", "x; drop table users", "", "a" * 129]:
        with pytest.raises(ValueError):
            check_model(bad)
    assert vector_type(768) == "vector(768)"
    assert vector_type(2000) == "vector(2000)"
    assert vector_type(3072) == "halfvec(3072)"  # text-embedding-3-large
    assert vector_type(5000) is None
    with pytest.raises(ValueError):
        vector_type(0)
    assert distance_sql(768) == (
        "(s.embedding::vector(768) <=> cast(:vector as vector(768)))"
    )
    assert "halfvec(3072)" in distance_sql(3072)
