"""Every model gen9-agent uses is reached through gen9-models, by alias (gen9-models/README.md).

The router keeps spend and budgets per user. It learns whose call it is from the
`x-litellm-end-user-id` header, which it reads with no configuration
(litellm/proxy/auth/auth_utils.py, STANDARD_CUSTOMER_ID_HEADERS). The run's user is held in a
context variable, and every request through the router's HTTP client carries it. Asyncio tasks copy
the context they start in, so the agent's calls, its subagents' and any embeddings of a run are
all attributed without being passed the user.
"""

import asyncio
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import quote

import httpx
import openai
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI

from . import partial_json
from .settings import Settings

# A streamed tool call's arguments parsed in linear time, wherever langchain-core parses them: a
# model that ran away inside one held the worker's event loop for minutes (partial_json.py)
partial_json.install()

END_USER_HEADER = "x-litellm-end-user-id"
# The model that served a call. The router answers with the alias in the body of a chat completion
# and an embedding, and with the model in this header (explore/models/NOTES.md)
SERVED_MODEL_HEADER = "x-litellm-model-name"
# LiteLLM names an OpenRouter deployment with its own prefix: openrouter/openai/gpt-6-luna
ROUTER_PREFIXES = ("openrouter/",)
# The router's web search tool (gen9-models/config.yaml, search_tools)
SEARCH_TOOL = "web"
MAX_SEARCH_RESULTS = 10
# Words that say nothing about a page on their own, in the languages people ask in most; with
# the words under three letters, they don't count towards a result being about its query
_STOPWORDS = frozenset(
    # A string split, not a list: sixty words read as prose, one to a line they don't
    "the and for with from that this what which who why how when where are was were not you your"  # noqa: SIM905
    " our its into about can will does has have der die das und ist ein eine den dem des mit von"
    " für les une est pour que qui dans sur par los las una por con del para".split()
)
# A search operator names where to look (site:, inurl:…), not what a page is about
_OPERATOR = re.compile(
    r"\b(?:site|inurl|intitle|intext|filetype|ext):\S*", re.IGNORECASE
)
_WORD = re.compile(r"\w+(?:[.\-]\w+)*")
# The embedding model for search by meaning (gen9-models/config.yaml)
EMBED_ALIAS = "embed"
# The reranker for search results, when one is configured (SEARCH_RERANK)
RERANK_ALIAS = "rerank"
RERANK_TIMEOUT_S = 3.0
_end_user: ContextVar[str | None] = ContextVar("gen9_model_end_user", default=None)
log = logging.getLogger(__name__)


@contextmanager
def on_behalf_of(user_sub: str) -> Iterator[None]:
    """Attribute every router call made inside the block to `user_sub`."""
    token = _end_user.set(user_sub)
    try:
        yield
    finally:
        _end_user.reset(token)


async def _stamp_end_user(request: httpx.Request) -> None:
    user = _end_user.get()
    if user is not None:
        request.headers[END_USER_HEADER] = user


def http_client() -> httpx.AsyncClient:
    """The HTTP client for every router call; the runtime opens and closes it."""
    return httpx.AsyncClient(
        # A reasoning turn can think for minutes before its first token
        timeout=httpx.Timeout(600, connect=10),
        event_hooks={"request": [_stamp_end_user]},
    )


def chat_model(
    settings: Settings,
    http: httpx.AsyncClient,
    alias: str,
    *,
    response_headers: bool = False,
) -> ChatOpenAI:
    """The chat model behind `alias`. With `response_headers`, each answer carries the router's
    headers in its `response_metadata`, for tracing to name the model that served it
    (`served_model`); whoever asks for them drops them, so they don't reach the checkpoints."""
    return ChatOpenAI(
        model=alias,
        base_url=f"{settings.gen9_models_url}/v1",
        api_key=settings.gen9_models_key,
        http_async_client=http,
        # The router already retries and falls back (gen9-models/config.yaml) and Temporal retries
        # the turn; a third layer here would only multiply attempts
        max_retries=0,
        # Each streamed answer ends with its usage, cached input apart, so tracing records what the
        # call cost. langchain-openai asks for it only from OpenAI's own address; without it
        # Langfuse counted the text itself and priced every input token as uncached, 3 to 9 times
        # the router's cost (docs/plans/manual-e2e.md, M3). The router streams it (probed)
        stream_usage=True,
        include_response_headers=response_headers,
        # The pages each of OpenAI's web searches consulted, so every answer can show its sources
        # (explore/models/NOTES.md). Only for that tool: other providers may reject `include`
        include=["web_search_call.action.sources"]
        if settings.web_search == "model"
        else None,
        # Gen9's context budget (CONTEXT_BUDGET_TOKENS), which Deep Agents' summarization reads:
        # the router's aliases have no profile of their own
        profile={"max_input_tokens": settings.context_budget_tokens},
    )


def served_model(message: BaseMessage) -> str | None:
    """The model that answered `message`, as Langfuse's price table names it (`openai/gpt-6-luna`):
    from the router's header when the answer carries its headers, else the body's model, which is
    the alias on Chat Completions."""
    metadata = message.response_metadata
    served = (metadata.get("headers") or {}).get(SERVED_MODEL_HEADER) or metadata.get(
        "model_name"
    )
    if not served:
        return None
    for prefix in ROUTER_PREFIXES:
        served = served.removeprefix(prefix)
    return served


def query_terms(query: str) -> frozenset[str]:
    """The words a result must share with `query` to be about it: lowercased, search operators
    left out, a hyphenated or dotted word also taken apart (browser-based: browser, based), and
    words under three letters and common ones dropped. Numbers of two digits or more stay: an
    RFC's number is what a search for it is about, while one digit is in nearly any address."""
    terms: set[str] = set()
    for word in _WORD.findall(_OPERATOR.sub(" ", query.lower())):
        for part in {word, *re.split(r"[.\-]", word)}:
            if (part.isdigit() and len(part) >= 2) or (
                len(part) >= 3 and part not in _STOPWORDS
            ):
                terms.add(part)
    return frozenset(terms)


def relevant(result: dict, terms: frozenset[str]) -> bool:
    """Whether a search result is about its query: its title, snippet and address share two of
    the query's words, or all of them when it has fewer. Scraped engines answer with pages
    unrelated to the query at times (searxng/searxng#6671): a question about an
    RFC came back with adult sites and help pages for other products, none sharing a word with
    it, and every relevant result shared two or more (docs/plans/gen9-learn.md, M9, F1)."""
    if not terms:
        return True
    text = " ".join(
        str(result.get(k) or "") for k in ("title", "snippet", "url")
    ).lower()
    return sum(1 for term in terms if term in text) >= min(2, len(terms))


def web_search_tool(settings: Settings, http: httpx.AsyncClient) -> BaseTool:
    """Web search through the router, for any `chat` model (WEB_SEARCH=router). The router's
    search API ignores `max_results` with SearXNG (explore/models/NOTES.md), so the list is cut
    here, after leaving out results that aren't about the query (`relevant`): neither the model
    nor the answer's Sources get them."""
    url = f"{settings.gen9_models_url}/v1/search/{SEARCH_TOOL}"
    key = settings.gen9_models_key.get_secret_value()

    # The results go to the model as content, and to the step as its sources (the artifact)
    @tool(response_format="content_and_artifact")
    async def web_search(query: str, max_results: int = 5) -> tuple[str, list[dict]]:
        """Search the web for current information. Returns the top results, each with its
        title, url and a snippet; cite the urls you use."""
        limit = max(1, min(max_results, MAX_SEARCH_RESULTS))
        response = await http.post(
            url,
            json={"query": query, "max_results": limit},
            headers={"Authorization": f"Bearer {key}"},
        )
        response.raise_for_status()
        found = response.json().get("results", [])
        terms = query_terms(query)
        about = [r for r in found if relevant(r, terms)]
        if len(about) < len(found):
            log.info(
                "web search: left out %d of %d results that aren't about the query",
                len(found) - len(about),
                len(found),
            )
        results = [
            {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("snippet")}
            for r in about[:limit]
        ]
        return json.dumps(results), results

    return web_search


class OverBudget(Exception):
    """The router refused a call made without an SDK because its user is over budget."""


async def _call(
    settings: Settings, http: httpx.AsyncClient, path: str, body: dict
) -> httpx.Response:
    """A call to the router made without an SDK. Raises OverBudget, or httpx.HTTPError when the
    router can't answer."""
    response = await http.post(
        f"{settings.gen9_models_url}{path}",
        json=body,
        headers={
            "Authorization": f"Bearer {settings.gen9_models_key.get_secret_value()}"
        },
    )
    if response.status_code == 429 and _budget_exceeded_body(response):
        raise OverBudget(response.text)
    response.raise_for_status()
    return response


async def embed(
    settings: Settings, http: httpx.AsyncClient, texts: list[str]
) -> tuple[list[list[float]], str | None]:
    """Embeddings through the `embed` alias, with the model that made them (the router answers
    with the alias; the real model is in `x-litellm-model-name`)."""
    response = await _call(
        settings, http, "/v1/embeddings", {"model": EMBED_ALIAS, "input": texts}
    )
    body = response.json()
    model = response.headers.get(SERVED_MODEL_HEADER) or body.get("model")
    return [item["embedding"] for item in body["data"]], model


async def rerank(
    settings: Settings, http: httpx.AsyncClient, query: str, documents: list[str]
) -> list[tuple[int, float]]:
    """The documents' indexes and scores, most relevant first, through the `rerank` alias (the
    router's Cohere-style rerank API). Raises TimeoutError after RERANK_TIMEOUT_S: with its
    reranker down, the router retries for 15-26 s before it answers 500 (measured), and search
    shouldn't wait."""
    async with asyncio.timeout(RERANK_TIMEOUT_S):
        response = await _call(
            settings,
            http,
            "/v1/rerank",
            {
                "model": RERANK_ALIAS,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
            },
        )
    return [(r["index"], r["relevance_score"]) for r in response.json()["results"]]


def _budget_exceeded_body(response: httpx.Response) -> bool:
    try:
        return response.json().get("error", {}).get("type") == "budget_exceeded"
    except ValueError:
        return False


def over_rate_limit(error: BaseException) -> bool:
    """The router refused the call because its user sent more requests this minute than
    GEN9_USER_RPM allows: HTTP 429 of type `throttling_error`, "Rate limit exceeded for
    end_user: …" (gen9-models' `gen9-user-default` budget; a provider's own limit says otherwise).
    It resets within the minute: the run waits for Retry rather than retrying at once."""
    return isinstance(error, openai.RateLimitError) and "exceeded for end_user" in str(
        error
    )


async def budget_of(settings: Settings, sub: str) -> dict | None:
    """A person's model budget as gen9-models' admin API has it (`spent_usd`, `max_usd`,
    `period`, `resets_at`), or None when it can't be read: what's shown then leaves it out
    (docs/plans/manual-e2e.md, P5-D1)."""
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            response = await http.get(
                f"{settings.gen9_models_admin_url}/users/{quote(sub, safe='')}/budget",
                headers={
                    "Authorization": f"Bearer {settings.gen9_models_key.get_secret_value()}"
                },
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def budget_exceeded(error: BaseException) -> bool:
    """The router refused the call because its user is over budget: HTTP 429, like a provider's
    rate limit, but with type `budget_exceeded` (explore/models/NOTES.md). Waiting won't help
    within the run, so it is not retried."""
    return (
        isinstance(error, openai.RateLimitError)
        and getattr(error, "type", None) == "budget_exceeded"
    )
