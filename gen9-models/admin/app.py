"""Gen9's admin API for the model router: what LiteLLM's own API can't do with the access Gen9
gives its callers, kept inside this stack (gen9-models/README.md, "Admin API").

- `POST /users/{sub}/erase`: delete everything the router keeps about one user. That is their
  spend logs, daily totals and end-user row: usage and cost, no messages. LiteLLM can't delete
  spend logs through its API, and its end-user deletion needs the master key, so this runs as SQL
  under a Postgres role that may only read and delete those three tables (scripts/ensure-keys.py
  grants it).
- `GET /users/{sub}/usage`: what the router keeps about one user, day by day (requests, tokens and
  cost per model; no messages), for their data export (GDPR Art. 15; docs/plans/manual-e2e.md,
  P5-B5). The same role may read those tables.
- Callers present gen9-agent's virtual key, compared in constant time with the one in .env; no
  other key, and no master key, is involved. gen9-agent's API's key may read usage, nothing else.

Runs on the pinned LiteLLM image's Python (FastAPI, uvicorn, psycopg 3), so it adds no image.
"""

import hmac
import os
from typing import Annotated

import psycopg
from fastapi import FastAPI, HTTPException, Path, Request, status
from psycopg import sql

AGENT_KEY = os.environ["GEN9_AGENT_MODELS_KEY"]
# gen9-agent's API's key (models-api.local.env): it builds a person's data export
API_KEY = os.environ.get("GEN9_AGENT_API_MODELS_KEY", "")
DATABASE_URL = os.environ["GEN9_ADMIN_DATABASE_URL"]
# The budget every user has until one of their own is set (config.yaml's max_end_user_budget_id)
DEFAULT_BUDGET = "gen9-user-default"

# Every table that keeps something about one end user, and the column naming them
USER_TABLES = (
    ("LiteLLM_SpendLogs", "end_user"),
    ("LiteLLM_DailyEndUserSpend", "end_user_id"),
    ("LiteLLM_EndUserTable", "user_id"),
)

app = FastAPI(
    title="gen9-models admin", openapi_url=None, docs_url=None, redoc_url=None
)


def _presented(request: Request) -> bytes:
    scheme, _, key = request.headers.get("authorization", "").partition(" ")
    return key.encode() if scheme.lower() == "bearer" else b""


def _caller_is_gen9_agent(request: Request) -> None:
    if not hmac.compare_digest(_presented(request), AGENT_KEY.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "gen9-agent's key is required"
        )


def _caller_may_read(request: Request) -> None:
    key = _presented(request)
    agent = hmac.compare_digest(key, AGENT_KEY.encode())
    api = bool(API_KEY) and hmac.compare_digest(key, API_KEY.encode())
    if not (agent or api):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "gen9-agent's key is required"
        )


# A person's id as gen9-agent sends it (their Keycloak sub): a NUL, which Postgres's text can't
# hold, made psycopg fail before the query, a 500 (docs/plans/manual-e2e.md, P6-B2). No control
# character, as long as the column: 422 otherwise, before any query
Sub = Annotated[str, Path(max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/users/{sub}/erase")
async def erase_user(sub: Sub, request: Request) -> dict:
    """Idempotent: erasing a user the router doesn't know deletes nothing and succeeds."""
    _caller_is_gen9_agent(request)
    deleted: dict[str, int] = {}
    async with (
        await psycopg.AsyncConnection.connect(DATABASE_URL) as conn,
        conn.transaction(),
    ):
        for table, column in USER_TABLES:
            cursor = await conn.execute(
                sql.SQL("delete from {} where {} = %s").format(
                    sql.Identifier(table), sql.Identifier(column)
                ),
                (sub,),
            )
            deleted[table] = cursor.rowcount
    return {"deleted": deleted}


@app.get("/users/{sub}/budget")
async def user_budget(sub: Sub, request: Request) -> dict:
    """A user's model budget: what they spent this period, its limit, how long a period is and
    when it resets (the budget's: the default one's is everyone's). A limit or time not set is
    None; a user the router doesn't know has spent nothing, on the default budget
    (docs/plans/manual-e2e.md, P5-D1)."""
    _caller_may_read(request)
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        # LiteLLM applies the default budget at request time without linking it to the user's
        # row (its budget_id stays null): the spend is the row's, the budget its link or the
        # default
        cursor = await conn.execute(
            "select coalesce(e.spend, 0), b.max_budget, b.budget_duration, b.budget_reset_at"
            ' from (select 1) one left join "LiteLLM_EndUserTable" e on e.user_id = %s'
            ' left join "LiteLLM_BudgetTable" b on b.budget_id = coalesce(e.budget_id, %s)',
            (sub, DEFAULT_BUDGET),
        )
        row = await cursor.fetchone()
    spent, limit, period, resets = row or (0, None, None, None)
    return {
        "spent_usd": float(spent or 0),
        "max_usd": None if limit is None else float(limit),
        "period": period,
        # LiteLLM keeps it in UTC, without a zone
        "resets_at": None
        if resets is None
        else f"{resets.isoformat(timespec='seconds')}Z",
    }


@app.get("/users/{sub}/usage")
async def user_usage(sub: Sub, request: Request) -> dict:
    """A user's model usage, day by day and model by model. A user the router doesn't know has no
    days."""
    _caller_may_read(request)
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cursor = await conn.execute(
            "select date, coalesce(model_group, model), sum(api_requests), sum(prompt_tokens),"
            ' sum(completion_tokens), sum(spend) from "LiteLLM_DailyEndUserSpend"'
            " where end_user_id = %s group by 1, 2 order by 1, 2",
            (sub,),
        )
        rows = await cursor.fetchall()
    return {
        "days": [
            {
                "date": str(day),
                "model": model,
                "requests": int(requests or 0),
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "cost_usd": float(cost or 0),
            }
            for day, model, requests, input_tokens, output_tokens, cost in rows
        ]
    }
