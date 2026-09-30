"""Search the caller's past chats: `GET /v1/search?q=…&mode=…`.

- `keyword`: BM25 over each run's question and answer (pg_textsearch), matching runs only.
- `semantic`: nearest by meaning (pgvector HNSW, cosine distance). The query is embedded through
  the model router's `embed` alias, on the caller's behalf, and compared only with chats
  embedded by the same model (search_index.py): while the reindex workflow re-embeds after a
  change of model, it finds the chats already done.
- In Chinese, Japanese, Thai, Lao, Khmer or Myanmar, whose words aren't spaced apart, `keyword`
  and `fuzzy` match the query's terms as text instead (UNSPACED), BM25 and trigrams seeing a
  whole sentence as one word.
- `fuzzy`: chats by a misspelled or partial title. pg_trgm's word similarity, which compares the
  query with the best-matching part of the title: a title is the whole first message, so plain
  similarity stays under its threshold for any short query.
- `hybrid` (the default): reciprocal rank fusion (k 60) of the keyword and semantic results;
  keyword alone when the query can't be embedded (router down, or the user over budget).

With SEARCH_RERANK, keyword, semantic and hybrid results go through the router's `rerank` alias:
the top 20 are reordered by the reranker, and each hit says which order it is in (`ranked_by`).
If the reranker fails, results keep the search's own order.

Each result is a chat, once, with its best-matching run (`run_id` and the snippet come from it):
people look for a chat, and the web app, the terminal, Gen9's MCP tool and the agent's search of
past chats all list chats. Only the caller's rows, and never a chat that is being deleted. How each query was chosen:
gen9-agent/explore/search/NOTES.md.
"""

import logging
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import Session
from ..model_router import OverBudget, embed, on_behalf_of, rerank
from ..models import User
from ..search_index import distance_sql
from ..settings import Settings
from ..users import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/search", tags=["search"])

RRF_K = 60  # reciprocal rank fusion's k, as langchain-postgres and the literature use
CANDIDATES = 20  # results each method contributes to the fusion
# Nearest neighbours are returned however far, so matches by meaning are kept only when their
# similarity is at least this share of the best one's (always the best itself). A share, not a
# number, holds across embedding models: relevant chats scored 0.42-0.69 and the rest at most
# 0.6 of the best, for OpenAI's and a local model (explore/search/NOTES.md)
RELEVANT_SHARE = 0.6
# How each embedding model wants a search query, and the similarity a match by meaning must reach
# with it besides the share, by the name the router gives the model that served `embed`. A model
# not listed gets the query as typed and no floor, since a floor belongs to one model's scores.
# Qwen3-Embedding: queries carry an instruction and documents none (its model card: without one,
# retrieval drops "by approximately 1% to 5%"). With it the 8B model scored the right chat from
# 0.437, wrong ones up to 0.419, and at most 0.334 where no chat answered; typed as is, the scores
# overlapped and unrelated chats came "by meaning" (gen9-learn.md, M9, F7)
QUERY_TEMPLATES = {
    "qwen3-embedding": "Instruct: Given a search phrase, retrieve the past chat it is about"
    "\nQuery: {query}"
}
SIMILARITY_FLOORS = {"qwen3-embedding-8b": 0.40}
NO_FLOOR = -1.0  # cosine similarity's lowest
BM25 = "to_bm25query(:q, 'chat_search_bm25')"  # bound parameters must name the index

# Scripts written without spaces between words: Chinese, Japanese, Thai, Lao, Khmer, Myanmar.
# Postgres's parser takes a run of them as one word, so BM25 and trigrams match only a whole
# sentence (manual-e2e.md, P4-C3); a query in them is matched as text instead, each of its terms
# anywhere in the question or answer. Exact, and a scan that one person's chats keep small
UNSPACED = re.compile(
    "[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    "\uf900-\ufaff\U00020000-\U0002fa1f]"
)
# How often a run holds the terms (:terms, lowercased): its score among the text matches
_OCCURS = (
    "(select sum((length(s.body) - length(replace(lower(s.body), term, ''))) / length(term))"
    " from unnest(cast(:terms as text[])) term)"
)
_HOLDS = "(select bool_and(strpos(lower(s.body), term) > 0) from unnest(cast(:terms as text[])) term)"

# Each returns one row per chat, its best-matching run: run_id, thread_id, title, body,
# created_at, score (higher is better). A search finds chats, as ChatGPT's does: by runs, one long
# chat's turns filled the results and pushed the others out (found by hand: manual-e2e.md, P2-I5).
# Collapsing keeps each chat's top run, as Elasticsearch's field collapsing does
KEYWORD = f"""
select run_id, thread_id, title, body, created_at, score from (
  select distinct on (s.thread_id)
         s.run_id, s.thread_id, t.title, s.body, s.created_at, -(s.body <@> {BM25}) as score
  from chat_search s join threads t on t.id = s.thread_id
  where s.user_id = :user and t.deleted_at is null and (s.body <@> {BM25}) < 0
  order by s.thread_id, s.body <@> {BM25}) best
order by score desc
limit :limit"""

# KEYWORD for a query in an unspaced script: the chats whose runs hold every term, most often first
KEYWORD_TEXT = f"""
select run_id, thread_id, title, body, created_at, score from (
  select distinct on (s.thread_id)
         s.run_id, s.thread_id, t.title, s.body, s.created_at, {_OCCURS}::float as score
  from chat_search s join threads t on t.id = s.thread_id
  where s.user_id = :user and t.deleted_at is null and {_HOLDS}
  order by s.thread_id, score desc) best
order by score desc
limit :limit"""

# The nearest runs a search by meaning reads before collapsing them to chats: exact at one
# person's scale, an HNSW scan past it (explore/search/NOTES.md)
NEAREST = 200


# Within the nearest rows (`score` a similarity, `best` the highest): those at the model's floor
# (`:floor`) that are the best or close enough to it. Window functions can't be in WHERE, so
# `best` comes from an inner select
_RELEVANT = f"(score >= :floor and (score >= {RELEVANT_SHARE} * best or score = best))"


def semantic_sql(dims: int) -> str:
    """Rows of the query's model only (`:model`), cast as its index is (search_index.py): a
    vector from another model isn't comparable, even at the same size."""
    distance = distance_sql(dims)
    return f"""
select run_id, thread_id, title, body, created_at, score from (
  select distinct on (thread_id) * from (
    select *, max(score) over () as best from (
      select s.run_id, s.thread_id, t.title, s.body, s.created_at, 1 - {distance} as score
      from chat_search s join threads t on t.id = s.thread_id
      where s.user_id = :user and t.deleted_at is null and s.embed_model = :model
      order by {distance}
      limit {NEAREST}) nearest) ranked
  where {_RELEVANT}
  order by thread_id, score desc) best
order by score desc
limit :limit"""


FUZZY = """
select null::uuid as run_id, t.id as thread_id, t.title, null as body, t.created_at,
       word_similarity(:q, t.title) as score
from threads t
where t.user_id = :user and t.deleted_at is null and :q <% t.title
order by score desc
limit :limit"""

# FUZZY for a query in an unspaced script: titles holding every term
FUZZY_TEXT = """
select null::uuid as run_id, t.id as thread_id, t.title, null as body, t.created_at, 1.0 as score
from threads t
where t.user_id = :user and t.deleted_at is null
  and (select bool_and(strpos(lower(t.title), term) > 0) from unnest(cast(:terms as text[])) term)
order by t.created_at desc
limit :limit"""


def hybrid_sql(dims: int, text_match: bool = False) -> str:
    """With `text_match`, its words half matches as text (UNSPACED): rank is then lower-is-better
    as BM25's is, so the fusion reads both alike."""
    distance = distance_sql(dims)
    rank = f"-{_OCCURS}" if text_match else f"s.body <@> {BM25}"
    matches = _HOLDS if text_match else f"(s.body <@> {BM25}) < 0"
    return f"""
with keyword as (
  select thread_id, run_id, row_number() over (order by rank) as r from (
    select distinct on (s.thread_id) s.thread_id, s.run_id, {rank} as rank
    from chat_search s join threads t on t.id = s.thread_id
    where s.user_id = :user and t.deleted_at is null and {matches}
    order by s.thread_id, rank) best
  order by rank limit {CANDIDATES}),
semantic as (
  select thread_id, run_id, row_number() over (order by score desc) as r from (
    select distinct on (thread_id) thread_id, run_id, score from (
      select *, max(score) over () as best from (
        select s.thread_id, s.run_id, 1 - {distance} as score
        from chat_search s join threads t on t.id = s.thread_id
        where s.user_id = :user and t.deleted_at is null and s.embed_model = :model
        order by {distance} limit {NEAREST}) nearest) ranked
    where {_RELEVANT}
    order by thread_id, score desc) best
  order by score desc limit {CANDIDATES}),
fused as (
  select thread_id, sum(1.0 / ({RRF_K} + r)) as score
  from (select thread_id, r from keyword union all select thread_id, r from semantic) both_lists
  group by thread_id)
-- A chat's run: its best match by words if it has one (its snippet shows them), else by meaning
select s.run_id, f.thread_id, t.title, s.body, s.created_at, f.score
from fused f
left join keyword k using (thread_id)
left join semantic v using (thread_id)
join chat_search s on s.run_id = coalesce(k.run_id, v.run_id)
join threads t on t.id = f.thread_id
order by f.score desc
limit :limit"""


Mode = Literal["hybrid", "keyword", "semantic", "fuzzy"]
# What a hit's score and order come from: its mode's query, or the reranker
RankedBy = Literal["hybrid", "keyword", "semantic", "fuzzy", "rerank"]
# The start of each chat the reranker reads: about 500 tokens, well inside its window
RERANK_CHARS = 2_000


class SearchHit(BaseModel):
    """A chat that matches, one per chat: its best-matching run's snippet (none for a title)."""

    thread_id: UUID
    title: str
    run_id: UUID | None  # None for a title match (fuzzy)
    snippet: str | None
    score: float  # higher is better; comparable among hits with the same `ranked_by`
    ranked_by: RankedBy
    created_at: datetime


OVER_BUDGET = "You've reached your model usage limit for now"
UNAVAILABLE = "Search by meaning is unavailable right now"


def _for_model[T](table: dict[str, T], model: str | None) -> T | None:
    name = (model or "").lower()
    return next((value for key, value in table.items() if key in name), None)


def query_text(query: str, model: str | None) -> str:
    """The query as `model` wants a search query put (QUERY_TEMPLATES)."""
    template = _for_model(QUERY_TEMPLATES, model)
    return template.format(query=query) if template else query


def similarity_floor(model: str | None) -> float:
    """The similarity below which nothing `model` finds is about the query (SIMILARITY_FLOORS)."""
    floor = _for_model(SIMILARITY_FLOORS, model)
    return NO_FLOOR if floor is None else floor


# The model that last served `embed` in this process: a query is put in its form before it is
# embedded, and embedded again when another one answered (the first search, or `embed` changed)
_served: dict[str, str | None] = {"embed": None}


async def _query_vector(
    settings: Settings, models: httpx.AsyncClient, query: str, user_sub: str
) -> tuple[str, str | None, int]:
    """The query's embedding as a pgvector literal, with the model that made it and its size.
    Raises HTTPException (429 over budget, 503 when the router can't answer)."""
    try:
        with on_behalf_of(user_sub):
            sent = query_text(query, _served["embed"])
            [vector], model = await embed(settings, models, [sent])
            if query_text(query, model) != sent:
                [vector], model = await embed(
                    settings, models, [query_text(query, model)]
                )
    except OverBudget:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, OVER_BUDGET) from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    _served["embed"] = model
    return "[" + ",".join(repr(x) for x in vector) + "]", model, len(vector)


SNIPPET_CHARS = 240


_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKS = re.compile(r"\*\*|__|`|^#+\s*", re.MULTILINE)


def _plain(text: str) -> str:
    """Markdown as the words a person reads: links become their text; emphasis, code and heading
    marks go; whitespace collapses."""
    return " ".join(_MARKS.sub("", _LINK.sub(r"\1", text)).split())


def snippet(body: str | None, q: str, limit: int = SNIPPET_CHARS) -> str | None:
    """What to show under a hit, as plain text: the text around the first word of the query found
    in the answer, else in the question, else the start of the answer. The row holds the question,
    a blank line, then the answer, and the question is usually the chat's title already."""
    if body is None:
        return None
    question, _, answer = body.partition("\n\n")
    question, answer = _plain(question), _plain(answer)
    flat = f"{question} {answer}" if answer else question
    answer_at = len(question) + 1 if answer else 0
    # Short words too in an unspaced script, where most words are one or two characters
    words = [
        w for w in re.findall(r"\w+", q.lower()) if len(w) > 2 or UNSPACED.search(w)
    ]
    lower = flat.lower()

    def first(start: int, stop: int) -> int | None:
        found = [i for w in words if 0 <= (i := lower.find(w, start, stop))]
        return min(found) if found else None

    match = first(answer_at, len(flat)) if answer else None
    if match is None:
        match = first(0, answer_at or len(flat))
    if match is None:
        start = answer_at
    else:
        start = max(answer_at if match >= answer_at else 0, match - 60)
        if start > (answer_at if match >= answer_at else 0):  # begin at a word
            start = flat.find(" ", start) + 1 or start
    text = flat[start : start + limit].rstrip()
    mid = start not in (0, answer_at)
    return ("…" if mid else "") + text + ("…" if start + limit < len(flat) else "")


async def _reranked(
    settings: Settings, models: httpx.AsyncClient, q: str, user_sub: str, rows: list
) -> list[tuple[dict, float]] | None:
    """The rows in the reranker's order with its scores, or None when it can't answer."""
    try:
        with on_behalf_of(user_sub):
            order = await rerank(
                settings,
                models,
                q,
                [row["body"][:RERANK_CHARS] for row in rows],
            )
    except (OverBudget, httpx.HTTPError, TimeoutError) as e:
        logger.warning(
            "search: rerank failed (%s), keeping the search's order", repr(e)
        )
        return None
    return [(rows[index], score) for index, score in order]


@router.get("", summary="Search your past chats")
async def search(
    request: Request,
    user: CurrentUser,
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    mode: Mode = "hybrid",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SearchHit]:
    runtime = request.app.state.runtime
    return await find(runtime.settings, runtime.models, session, user, q, mode, limit)


async def find(
    settings: Settings,
    models: httpx.AsyncClient,
    session: AsyncSession,
    user: User,
    q: str,
    mode: Mode = "hybrid",
    limit: int = 10,
) -> list[SearchHit]:
    """The search itself, for the endpoint, Gen9's MCP server (mcp_server.py) and the agent's
    search of past chats (past_chats.py). Raises HTTPException when a search by meaning can't be
    made (over budget, the router down)."""
    reranking = settings.search_rerank and mode != "fuzzy"
    params: dict = {
        "q": q,
        "user": user.id,
        "limit": max(limit, CANDIDATES) if reranking else limit,
    }
    ranked_by: RankedBy = mode
    text_match = bool(UNSPACED.search(q))
    if text_match:
        params["terms"] = [term.lower() for term in q.split()]
    keyword = KEYWORD_TEXT if text_match else KEYWORD
    sql = {"keyword": keyword, "fuzzy": FUZZY_TEXT if text_match else FUZZY}.get(
        mode, ""
    )
    if mode in ("semantic", "hybrid"):
        try:
            params["vector"], params["model"], dims = await _query_vector(
                settings, models, q, user.sub
            )
            params["floor"] = similarity_floor(params["model"])
        except HTTPException as e:
            if mode == "semantic":
                raise
            # Words still match without a model: hybrid falls back to keyword alone
            logger.warning("search: %s, answering by keyword alone", e.detail)
            sql, ranked_by = keyword, "keyword"
        else:
            sql = (
                semantic_sql(dims)
                if mode == "semantic"
                else hybrid_sql(dims, text_match)
            )
            # A filtered nearest-neighbour search keeps scanning until it has enough rows
            # (pgvector 0.8 iterative scans). A generic plan couldn't use the model's partial
            # index (explore/search/NOTES.md), so this query is always planned for its values
            await session.execute(text("set local hnsw.iterative_scan = relaxed_order"))
            await session.execute(text("set local plan_cache_mode = force_custom_plan"))
    rows = list((await session.execute(text(sql), params)).mappings().all())
    scored = [(row, float(row["score"])) for row in rows]
    if reranking and len(rows) > 1:
        reordered = await _reranked(settings, models, q, user.sub, rows)
        if reordered is not None:
            scored, ranked_by = reordered, "rerank"
    return [
        SearchHit(
            thread_id=row["thread_id"],
            title=row["title"],
            run_id=row["run_id"],
            snippet=snippet(row["body"], q),
            score=score,
            ranked_by=ranked_by,
            created_at=row["created_at"],
        )
        for row, score in scored[:limit]
    ]
