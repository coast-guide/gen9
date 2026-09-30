"""Matches by meaning (api/search.py; gen9-learn.md, M9, F7): a query goes to `embed` in its
model's own form, and a match must reach that model's floor as well as the share of the best.
A model not listed keeps the query as typed and has no floor."""

import pytest

from gen9_agent.api import search
from gen9_agent.api.search import NO_FLOOR, query_text, similarity_floor

pytestmark = pytest.mark.asyncio

QWEN_8B = "openrouter/qwen/qwen3-embedding-8b"


async def test_qwen3_queries_carry_its_instruction_and_others_go_as_typed() -> None:
    told = query_text("lighthouses", QWEN_8B)
    assert told.startswith("Instruct: ") and told.endswith("\nQuery: lighthouses")
    assert query_text("lighthouses", "qwen/qwen3-embedding-0.6b") == told
    for model in ("openai/text-embedding-3-small", "ollama/embeddinggemma", None):
        assert query_text("lighthouses", model) == "lighthouses"
    # A query's own braces are its text, not the template's
    assert query_text("{query} {x}", QWEN_8B).endswith("Query: {query} {x}")


async def test_the_floor_is_the_measured_models_only() -> None:
    assert similarity_floor(QWEN_8B) == 0.40
    for model in ("qwen/qwen3-embedding-0.6b", "openai/text-embedding-3-small", None):
        assert similarity_floor(model) == NO_FLOOR


async def test_the_relevance_rule_needs_the_floor_and_the_share() -> None:
    assert (
        ":floor" in search._RELEVANT and str(search.RELEVANT_SHARE) in search._RELEVANT
    )
    assert search._RELEVANT in search.semantic_sql(1024)
    assert search._RELEVANT in search.hybrid_sql(1024)


class Router:
    """`embed` as the router answers: the model named, and what it was asked."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.asked: list[str] = []

    async def __call__(self, settings, http, texts):
        self.asked += texts
        return [[0.5, 0.5]], self.model


async def test_the_first_search_learns_the_model_and_asks_again_in_its_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = Router(QWEN_8B)
    monkeypatch.setattr(search, "embed", router)
    monkeypatch.setitem(search._served, "embed", None)

    vector, model, dims = await search._query_vector(None, None, "numbers", "sub")  # ty: ignore[invalid-argument-type]
    assert router.asked == ["numbers", query_text("numbers", QWEN_8B)]
    assert (vector, model, dims) == ("[0.5,0.5]", QWEN_8B, 2)

    # Known from then on: one call, in its form
    router.asked.clear()
    await search._query_vector(None, None, "tides", "sub")  # ty: ignore[invalid-argument-type]
    assert router.asked == [query_text("tides", QWEN_8B)]

    # `embed` changed to a model with no template: asked again as typed
    router.model, router.asked = "ollama/embeddinggemma", []
    _, model, _ = await search._query_vector(None, None, "tides", "sub")  # ty: ignore[invalid-argument-type]
    assert router.asked == [query_text("tides", QWEN_8B), "tides"]
    assert model == "ollama/embeddinggemma" and search._served["embed"] == model
