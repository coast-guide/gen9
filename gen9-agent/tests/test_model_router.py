"""Calls to the model router carry the run's user, and a budget refusal is told apart from a rate
limit (model_router.py). Both through real httpx and openai objects, with a mock transport."""

import asyncio
import json

import httpx
import openai
import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from pydantic import SecretStr

from gen9_agent.agent import name_served_model
from gen9_agent.model_router import (
    END_USER_HEADER,
    OverBudget,
    _stamp_end_user,
    budget_exceeded,
    chat_model,
    embed,
    http_client,
    on_behalf_of,
    over_rate_limit,
    query_terms,
    relevant,
    rerank,
    web_search_tool,
)
from gen9_agent.runs.store import (
    PUBLIC_BUDGET_ERROR,
    PUBLIC_ERROR,
    PUBLIC_RATE_LIMITED,
    public_error,
    retry_reason,
)
from gen9_agent.settings import Settings

pytestmark = pytest.mark.asyncio


def recording_client(seen: list[str | None]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get(END_USER_HEADER))
        return httpx.Response(200, json={})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [_stamp_end_user]},
    )


async def test_calls_in_a_run_carry_its_user_and_others_none():
    seen: list[str | None] = []
    async with recording_client(seen) as client:
        await client.get("http://router/outside")
        with on_behalf_of("sub-1"):
            await client.get("http://router/inside")
            # a task started inside the run (LangGraph runs nodes as tasks) inherits the user
            await asyncio.create_task(client.get("http://router/in-a-task"))
        await client.get("http://router/after")
    assert seen == [None, "sub-1", "sub-1", None]


async def test_concurrent_runs_keep_their_own_user():
    seen: list[str | None] = []
    async with recording_client(seen) as client:

        async def run(sub: str) -> None:
            with on_behalf_of(sub):
                for _ in range(3):
                    await client.get(f"http://router/{sub}")
                    await asyncio.sleep(0)

        await asyncio.gather(run("a"), run("b"))
    assert sorted(seen) == ["a", "a", "a", "b", "b", "b"]


def status_error(status: int, error: dict) -> openai.APIStatusError:
    request = httpx.Request("POST", "http://router/v1/responses")
    response = httpx.Response(status, json={"error": error}, request=request)
    return openai.AsyncOpenAI(
        api_key="x", base_url="http://router/v1"
    )._make_status_error_from_response(response)


async def test_budget_refusal_is_told_apart_from_a_rate_limit():
    # What gen9-models answers for a user over budget (explore/models/NOTES.md)
    refused = status_error(
        429,
        {
            "message": "ExceededBudget: End User=sub-1 over budget.",
            "type": "budget_exceeded",
            "code": "429",
        },
    )
    limited = status_error(
        429, {"message": "Rate limit reached", "type": "rate_limit_error"}
    )
    assert isinstance(refused, openai.RateLimitError) and budget_exceeded(refused)
    assert isinstance(limited, openai.RateLimitError) and not budget_exceeded(limited)
    assert not budget_exceeded(RuntimeError("budget_exceeded"))


async def test_a_person_over_their_requests_per_minute_is_told_apart_from_a_provider():
    """GEN9_USER_RPM: the router's refusal, as it answered live (P2-H1), waits for Retry with its
    reason; a provider's own rate limit is still retried."""
    over = status_error(
        429,
        {
            "message": "Rate limit exceeded for end_user: sub-1. Limit type: requests. "
            "Current limit: 2, Remaining: 0. Limit resets at: 2026-09-26 21:37:30 UTC",
            "type": "throttling_error",
            "param": None,
            "code": "429",
        },
    )
    provider = status_error(
        429, {"message": "Rate limit reached", "type": "rate_limit_error"}
    )
    assert over_rate_limit(over) and not budget_exceeded(over)
    assert not over_rate_limit(provider)
    assert retry_reason(f"ApplicationError: RateLimited: {over}") == PUBLIC_RATE_LIMITED
    assert public_error(f"ApplicationError: RateLimited: {over}") == PUBLIC_RATE_LIMITED


async def test_people_see_why_a_run_failed_only_for_budgets():
    # What the run's row keeps for a budget refusal: the innermost cause, the router's error (live)
    refused = (
        "ApplicationError: RateLimitError: Error code: 429 - {'error': {'message': "
        "'ExceededBudget: End User=sub-1 over budget.', 'type': 'budget_exceeded', "
        "'param': None, 'code': '429'}}"
    )
    assert public_error(refused) == PUBLIC_BUDGET_ERROR
    assert (
        public_error("ApplicationError: BudgetExceeded: Error code: 429 …")
        == PUBLIC_BUDGET_ERROR
    )
    assert public_error("ApplicationError: BadRequestError: …") == PUBLIC_ERROR
    assert public_error(None) == PUBLIC_ERROR


async def test_web_search_goes_through_the_router_by_alias_and_is_cut_to_size():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # SearXNG through the router ignores max_results and answers with everything it found
        results = [
            {
                "title": f"Postgres 18 notes, part {i}",
                "url": f"https://e.test/{i}",
                "snippet": "s",
                "date": None,
            }
            for i in range(30)
        ]
        return httpx.Response(200, json={"object": "search", "results": results})

    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-agent")
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [_stamp_end_user]},
    )
    async with client:
        search = web_search_tool(settings, client)
        with on_behalf_of("sub-1"):
            # As the agent calls it: a tool call, answered by a message with the results as its
            # artifact (the step's sources)
            reply = await search.ainvoke(
                {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "web_search",
                    "args": {"query": "postgres 18", "max_results": 3},
                }
            )
        found = json.loads(reply.content)
        capped = json.loads(
            await search.ainvoke({"query": "postgres notes", "max_results": 50})
        )
    request = seen[0]
    assert request.url == "http://router/v1/search/web"
    assert request.headers["authorization"] == "Bearer sk-agent"
    assert request.headers[END_USER_HEADER] == "sub-1"
    assert json.loads(request.content) == {"query": "postgres 18", "max_results": 3}
    assert found == [
        {
            "title": f"Postgres 18 notes, part {i}",
            "url": f"https://e.test/{i}",
            "snippet": "s",
        }
        for i in range(3)
    ]
    assert reply.artifact == found  # the step's sources
    assert len(capped) == 10  # at most MAX_SEARCH_RESULTS


async def test_web_search_leaves_out_results_that_are_not_about_the_query():
    # What SearXNG's engines answered (docs/plans/gen9-learn.md, M9, F1): pages
    # about the query, and junk sharing none of its words (adult sites among it, here stand-ins)
    answered = [
        {
            "title": "YouTube TV Help",
            "url": "https://support.google.com/youtubetv/",
            "snippet": "Get help",
        },
        {
            "title": "RFC 10017: OAuth 2.0 for Browser-Based Applications | RFC Editor",
            "url": "https://www.rfc-editor.org/info/rfc10017/",
            "snippet": "",
        },
        {
            "title": "Adult forum, page 167",
            "url": "https://forum.example/threads/387236/page-167",
            "snippet": "Post a picture",
        },
        {
            "title": "OAuth 2.0 for Browser-Based Apps",
            "url": "https://oauth.net/2/browser-based-apps/",
            "snippet": "RFC 10017",
        },
        {
            "title": "How to get help in Windows",
            "url": "https://support.microsoft.com/en-us/windows/",
            "snippet": "",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "search", "results": answered})

    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-agent")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        search = web_search_tool(settings, client)
        reply = await search.ainvoke(
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "web_search",
                "args": {
                    "query": 'site:rfc-editor.org/rfc/rfc10017 "RFC 10017" OAuth browser-based BFF',
                    "max_results": 5,
                },
            }
        )
    kept = [r["url"] for r in json.loads(reply.content)]
    assert kept == [
        "https://www.rfc-editor.org/info/rfc10017/",
        "https://oauth.net/2/browser-based-apps/",
    ]
    assert [r["url"] for r in reply.artifact] == kept  # nor among the answer's Sources


async def test_query_terms_and_what_counts_as_about_the_query():
    terms = query_terms(
        "site:example.org RFC 10017 OAuth 2.0 browser-based apps, the BFF"
    )
    # the operator's words, stop words, short words and single digits don't count
    assert terms == frozenset(
        {
            "rfc",
            "10017",
            "oauth",
            "2.0",
            "browser-based",
            "browser",
            "based",
            "apps",
            "bff",
        }
    )
    assert query_terms("Welche Türme warnen nachts Schiffe?") == frozenset(
        {"welche", "türme", "warnen", "nachts", "schiffe"}
    )
    assert relevant({"title": "Browser apps and OAuth"}, terms)
    assert not relevant(
        {"title": "OAuth", "url": "https://e.test/"}, terms
    )  # one word only
    assert relevant(
        {"title": "Anything"}, frozenset()
    )  # a query of stop words keeps all
    one = query_terms("lighthouses")
    assert relevant({"snippet": "Lighthouses of Maine"}, one)
    assert not relevant({"title": "Harbours"}, one)


async def test_embeddings_go_by_alias_and_report_the_real_model():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if json.loads(request.content)["input"] == ["over"]:
            error = {
                "message": "ExceededBudget",
                "type": "budget_exceeded",
                "code": "429",
            }
            return httpx.Response(429, json={"error": error})
        if json.loads(request.content)["input"] == ["down"]:
            return httpx.Response(503, json={"error": {"message": "no deployments"}})
        # The router answers with the alias; the real model is in a header (explore/models/NOTES.md)
        return httpx.Response(
            200,
            json={"model": "embed", "data": [{"embedding": [0.1, 0.2]}]},
            headers={"x-litellm-model-name": "text-embedding-3-small"},
        )

    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-agent")
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [_stamp_end_user]},
    )
    async with client:
        with on_behalf_of("sub-1"):
            vectors, model = await embed(settings, client, ["hello"])
        with pytest.raises(OverBudget):
            await embed(settings, client, ["over"])
        with pytest.raises(httpx.HTTPStatusError):
            await embed(settings, client, ["down"])
    assert (vectors, model) == ([[0.1, 0.2]], "text-embedding-3-small")
    request = seen[0]
    assert request.url == "http://router/v1/embeddings"
    assert json.loads(request.content) == {"model": "embed", "input": ["hello"]}
    assert request.headers["authorization"] == "Bearer sk-agent"
    assert request.headers[END_USER_HEADER] == "sub-1"


async def test_rerank_goes_by_alias_and_returns_the_rerankers_order():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body["query"] == "over":
            error = {"message": "ExceededBudget", "type": "budget_exceeded"}
            return httpx.Response(429, json={"error": error})
        # The router's Cohere-style answer, most relevant first (gen9-models, llama.cpp behind it)
        results = [
            {"index": 2, "relevance_score": -0.03},
            {"index": 0, "relevance_score": -0.09},
            {"index": 1, "relevance_score": -0.12},
        ]
        return httpx.Response(200, json={"id": "r", "results": results})

    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-api")
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [_stamp_end_user]},
    )
    async with client:
        with on_behalf_of("sub-1"):
            order = await rerank(settings, client, "retry", ["a", "b", "c"])
        with pytest.raises(OverBudget):
            await rerank(settings, client, "over", ["a", "b"])
    assert order == [(2, -0.03), (0, -0.09), (1, -0.12)]
    request = seen[0]
    assert request.url == "http://router/v1/rerank"
    assert json.loads(request.content) == {
        "model": "rerank",
        "query": "retry",
        "documents": ["a", "b", "c"],
        "top_n": 3,
    }
    assert request.headers["authorization"] == "Bearer sk-api"
    assert request.headers[END_USER_HEADER] == "sub-1"


async def test_a_stalled_reranker_times_out(monkeypatch):
    # With its reranker down the router retries for 15-26 s before answering (measured)
    async def stalled(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(500)

    monkeypatch.setattr("gen9_agent.model_router.RERANK_TIMEOUT_S", 0.05)
    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-api")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as client:
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await rerank(settings, client, "q", ["a", "b"])
    assert asyncio.get_running_loop().time() - started < 1


def routed_answer(request: httpx.Request) -> httpx.Response:
    """A chat completion as the router sends it, streamed or not: the alias in the body, the
    model that served it in a header, and a provider's cookie among the other headers."""
    headers = {
        "x-litellm-model-name": "openrouter/openai/gpt-6-luna",
        "llm_provider-set-cookie": "__cf_bm=secret",
    }
    base = {"id": "c1", "created": 0, "model": "chat"}
    if not json.loads(request.content).get("stream"):
        message = {"role": "assistant", "content": "OK"}
        choice = {"index": 0, "message": message, "finish_reason": "stop"}
        body = {**base, "object": "chat.completion", "choices": [choice]}
        return httpx.Response(200, json=body, headers=headers)
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"content": "OK"}}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    events = "".join(
        f"data: {json.dumps({**c, 'object': 'chat.completion.chunk'})}\n\n"
        for c in chunks
    )
    return httpx.Response(
        200,
        text=events + "data: [DONE]\n\n",
        headers={**headers, "content-type": "text/event-stream"},
    )


async def test_a_traced_answer_names_the_model_that_served_it_and_keeps_no_headers():
    settings = Settings.model_construct(
        gen9_models_url="http://router",
        gen9_models_key=SecretStr("sk-agent"),
        web_search="router",
    )
    streams: list[bool] = []

    def answer_and_record(request: httpx.Request) -> httpx.Response:
        streams.append(bool(json.loads(request.content).get("stream")))
        return routed_answer(request)

    transport = httpx.MockTransport(answer_and_record)
    seen: list[LLMResult] = []

    class Tracer(AsyncCallbackHandler):
        async def on_llm_end(self, response: LLMResult, **kwargs) -> None:
            name_served_model(response)
            seen.append(response)

    async with httpx.AsyncClient(transport=transport) as client:
        traced = chat_model(settings, client, "chat", response_headers=True)
        answer = await traced.ainvoke("Say OK.", config={"callbacks": [Tracer()]})
        # The agent's calls stream (LangGraph asks for it)
        streamed = await traced.bind(stream=True).ainvoke(
            "Say OK.", config={"callbacks": [Tracer()]}
        )
        untraced = await chat_model(settings, client, "chat").ainvoke("Say OK.")
    assert streams == [False, True, False]
    # Langfuse's handler reads llm_output: the model, without the router's own prefix
    assert [r.llm_output["model_name"] for r in seen] == ["openai/gpt-6-luna"] * 2
    # The headers, a provider's cookie among them, never reach the conversation
    for message in (answer, streamed):
        assert message.text == "OK" and "headers" not in message.response_metadata
    # Without tracing the headers aren't asked for, and the body names only the alias
    assert "headers" not in untraced.response_metadata
    assert untraced.response_metadata["model_name"] == "chat"


async def test_a_chat_call_keeps_the_router_clients_timeouts():
    """A router that doesn't answer fails the call in seconds: the OpenAI client langchain-openai
    makes would send every request with no timeout at all, in place of the HTTP client's, and an
    unanswered connection then waited for the kernel to give up, two minutes and more (U3,
    docs/plans/deploy.md)."""
    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-agent")
    )
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.extensions["timeout"])
        return httpx.Response(503)

    async with http_client() as made:
        expected = made.timeout
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=expected
    ) as client:
        with pytest.raises(openai.APIStatusError):
            await chat_model(settings, client, "chat").ainvoke("hi")
    assert sent == [expected.as_dict()]
    assert expected.connect == 10


async def test_a_streamed_answer_asks_for_its_usage_and_carries_cached_input():
    """Langfuse prices what the handler hands it: without usage it counts the text itself and
    prices all input as uncached (M3, docs/plans/manual-e2e.md)."""
    settings = Settings.model_construct(
        gen9_models_url="http://router", gen9_models_key=SecretStr("sk-agent")
    )
    asked: list[dict] = []

    def answer_with_usage(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append(body.get("stream_options") or {})
        base = {
            "id": "c1",
            "created": 0,
            "model": "chat",
            "object": "chat.completion.chunk",
        }
        usage = {
            "prompt_tokens": 1617,
            "completion_tokens": 5,
            "total_tokens": 1622,
            "prompt_tokens_details": {"cached_tokens": 1614},
        }
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"content": "cache"}}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {**base, "choices": [], "usage": usage},
        ]
        events = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
        return httpx.Response(
            200,
            text=events + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(answer_with_usage)
    ) as client:
        answer = (
            await chat_model(settings, client, "chat").bind(stream=True).ainvoke("hi")
        )
    assert asked == [{"include_usage": True}]
    usage = answer.usage_metadata
    assert usage is not None and usage["input_tokens"] == 1617
    assert usage.get("input_token_details", {}).get("cache_read") == 1614


async def test_a_turn_that_doesnt_fit_fails_once_in_words_and_tiny_budgets_are_refused():
    """P2-H4: at CONTEXT_BUDGET_TOKENS=8000 every turn raised ContextOverflowError, was retried
    three times and said "The model provider didn't answer"."""
    from langchain_core.exceptions import ContextOverflowError
    from pydantic import ValidationError

    from gen9_agent.runs.executor import PERMANENT_ERRORS
    from gen9_agent.runs.store import PUBLIC_TOO_LONG
    from gen9_agent.settings import Settings

    assert issubclass(ContextOverflowError, PERMANENT_ERRORS)
    kept = (
        "ApplicationError: ContextOverflowError: Context remains above the input budget "
        "after compaction; reduce input, tools, or configured output tokens."
    )
    assert public_error(kept) == PUBLIC_TOO_LONG
    with pytest.raises(ValidationError, match="12000"):
        Settings.model_validate({"context_budget_tokens": 8000})
    # e2e/context.mjs's small budget stays allowed: the floor is 12,000
    floor = Settings.model_fields["context_budget_tokens"].metadata
    assert [m.ge for m in floor if hasattr(m, "ge")] == [12_000]


async def test_over_budget_says_when_the_limit_resets_if_it_knows() -> None:
    # P5-D1: the executor adds the reset time the router's admin API gave
    refused = "ApplicationError: BudgetExceeded: Error code: 429 - budget_exceeded"
    assert public_error(f"{refused} (resets 2026-10-01T00:00:00Z)") == (
        "You've reached your model usage limit. It resets on 1 October 2026 at 00:00 UTC: "
        "try again then, or ask an admin."
    )
    assert retry_reason(f"{refused} (resets 2026-10-31T00:00:00Z)").startswith(
        "You've reached your model usage limit. It resets on 31 October 2026"
    )
    # Without it, as before
    assert public_error(refused) == PUBLIC_BUDGET_ERROR
