"""Bring the router's Gen9-side setup in line with .env; safe on every start.

- gen9-agent's key: created once, with the value init-env.sh generated, so gen9-agent's settings
  file can be written before the router first runs. At most GEN9_AGENT_BUDGET_USD a day (default
  5), whoever it works for: the backstop under each person's budget, so no loop or bug spends
  without end. Set on every start and read back.
- gen9-agent's API's key: the same, but it may only embed and rerank (search queries): the
  `embed` and `rerank` aliases and no search tool. Its scope is set again on every start and read back; a start fails if the
  router doesn't hold it.
- gen9-agent's evals' key (GEN9_EVALS_MODELS_KEY): only the `chat` alias, for the judge, no search
  tool, and at most GEN9_EVALS_BUDGET_USD a day. Set and read back the same way.
- The budget `gen9-user-default`, which config.yaml names as every end user's default
  (`max_end_user_budget_id`): GEN9_USER_BUDGET_USD per GEN9_USER_BUDGET_PERIOD, and GEN9_USER_RPM
  requests a minute; an empty setting means no such limit. It applies to each user gen9-agent calls
  on behalf of (x-litellm-end-user-id) who has no budget of their own.
- The Postgres role `gen9_admin`, which the admin API (admin/app.py) connects as: it may read and
  delete the three tables that keep something about one user, and nothing else. Its password
  follows GEN9_ADMIN_DB_PASSWORD. Granted after LiteLLM's own migrations, which create the tables.

Runs in the LiteLLM image, which ships httpx and psycopg 3 (LiteLLM dependencies).
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

import httpx
import psycopg
from psycopg import sql

BUDGET_ID = "gen9-user-default"
# What the API's key may call (search: embed the query, rerank the results). `models` covers every model route but not the search API, whose
# tools are an allow-list of their own where an empty list means all: naming a tool that doesn't
# exist leaves none (gen9-agent/explore/models/NOTES.md, "How narrow a virtual key can be")
API_KEY_ALIAS = "gen9-agent-api"
API_KEY_SCOPE = {
    "models": ["embed", "rerank"],
    "object_permission": {"search_tools": ["none"]},
}
# The evals' judge (gen9-agent/evals/graders.py) calls the `chat` alias, and nothing else
EVALS_KEY_ALIAS = "gen9-evals"
EVALS_KEY_SCOPE = {
    "models": ["chat"],
    "object_permission": {"search_tools": ["none"]},
}
ADMIN_ROLE = "gen9_admin"
# The tables the admin API erases a user from (admin/app.py, USER_TABLES)
USER_TABLES = ("LiteLLM_SpendLogs", "LiteLLM_DailyEndUserSpend", "LiteLLM_EndUserTable")
# What it only reads: a user's budget, its limit and when it resets (admin/app.py, user_budget)
READ_TABLES = ("LiteLLM_BudgetTable",)


async def ensure_admin_role() -> None:
    async with await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], autocommit=True
    ) as conn:
        exists = await (
            await conn.execute(
                "select 1 from pg_roles where rolname = %s", (ADMIN_ROLE,)
            )
        ).fetchone()
        if not exists:
            await conn.execute(
                sql.SQL("create role {} login").format(sql.Identifier(ADMIN_ROLE))
            )
        await conn.execute(
            sql.SQL("alter role {} with login password {}").format(
                sql.Identifier(ADMIN_ROLE),
                sql.Literal(os.environ["GEN9_ADMIN_DB_PASSWORD"]),
            )
        )
        cursor = await conn.execute("select current_database()")
        (database,) = await cursor.fetchone() or ("litellm",)
        await conn.execute(
            sql.SQL("grant connect on database {} to {}").format(
                sql.Identifier(database), sql.Identifier(ADMIN_ROLE)
            )
        )
        await conn.execute(
            sql.SQL("grant usage on schema public to {}").format(
                sql.Identifier(ADMIN_ROLE)
            )
        )
        await conn.execute(
            sql.SQL("grant select, delete on {} to {}").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in USER_TABLES),
                sql.Identifier(ADMIN_ROLE),
            )
        )
        await conn.execute(
            sql.SQL("grant select on {} to {}").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in READ_TABLES),
                sql.Identifier(ADMIN_ROLE),
            )
        )
    print(
        f"role {ADMIN_ROLE}: may read and delete {', '.join(USER_TABLES)}; "
        f"may read {', '.join(READ_TABLES)}"
    )


def budget_from_env() -> dict:
    """The default budget's limits; None clears a limit (/budget/update stores nulls)."""
    usd = os.environ.get("GEN9_USER_BUDGET_USD", "").strip()
    rpm = os.environ.get("GEN9_USER_RPM", "").strip()
    return {
        "budget_id": BUDGET_ID,
        "max_budget": float(usd) if usd else None,
        "budget_duration": (
            os.environ.get("GEN9_USER_BUDGET_PERIOD", "").strip() or None
        )
        if usd
        else None,
        "rpm_limit": int(rpm) if rpm else None,
    }


async def retire(router: httpx.AsyncClient, alias: str) -> bool:
    """Frees `alias` for a new key when .env names one (a rotation, docs/secrets.md): an older
    key holding it is renamed `<alias>-retired-<UTC time>` and keeps working, scope and budget
    alike, until the operator deletes it once gen9-agent has the new one. LiteLLM refuses a
    second key with the same alias (HTTP 400)."""
    held = await router.get(
        "/key/list", params={"key_alias": alias, "return_full_object": "true"}
    )
    ok = held.status_code == 200
    for old in held.json().get("keys", []) if ok else []:
        retired = f"{alias}-retired-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        renamed = await router.post(
            "/key/update", json={"key": old["token"], "key_alias": retired}
        )
        ok = ok and renamed.status_code == 200
        print(
            f"{alias}'s old key renamed {retired}: delete it once gen9-agent has the new one"
        )
    if not ok:
        print(
            f"could not free the alias {alias}: HTTP {held.status_code}",
            file=sys.stderr,
        )
    return ok


async def ensure_scoped_key(
    router: httpx.AsyncClient,
    key: str,
    alias: str,
    scope: dict,
    what: str,
    limits: dict | None = None,
) -> bool:
    """A key limited to `scope` (and `limits`: a budget), created or brought in line, then read
    back: a start fails if the router doesn't hold what was asked."""
    metadata = {"owner": alias}
    wanted = {**scope, **(limits or {})}
    if (await router.get("/key/info", params={"key": key})).status_code == 200:
        # A changed scope reaches the router's key cache within about a minute (NOTES.md)
        response = await router.post(
            "/key/update", json={"key": key, "metadata": metadata, **wanted}
        )
    else:
        if not await retire(router, alias):
            return False
        response = await router.post(
            "/key/generate",
            json={"key": key, "key_alias": alias, "metadata": metadata, **wanted},
        )
    if response.status_code != 200:
        print(
            f"could not set {alias}'s key: HTTP {response.status_code} {response.text[:200]}",
            file=sys.stderr,
        )
        return False
    info = (await router.get("/key/info", params={"key": key})).json().get("info", {})
    held = {
        "models": info.get("models"),
        "object_permission": {
            "search_tools": (info.get("object_permission") or {}).get("search_tools")
        },
        **{k: info.get(k) for k in (limits or {})},
    }
    if held != wanted:
        print(f"{alias}'s key has {held}, not {wanted}", file=sys.stderr)
        return False
    print(f"{alias}'s key: {what}")
    return True


def agent_limits() -> dict:
    """gen9-agent's key's daily budget: GEN9_AGENT_BUDGET_USD (default 5; empty: none)."""
    usd = os.environ.get("GEN9_AGENT_BUDGET_USD", "5").strip()
    return (
        {"max_budget": float(usd), "budget_duration": "1d"}
        if usd
        else {"max_budget": None, "budget_duration": None}
    )


def evals_limits() -> dict:
    """The evals' key's daily budget: GEN9_EVALS_BUDGET_USD (default 5; empty: none)."""
    usd = os.environ.get("GEN9_EVALS_BUDGET_USD", "5").strip()
    return (
        {"max_budget": float(usd), "budget_duration": "1d"}
        if usd
        else {"max_budget": None, "budget_duration": None}
    )


async def main() -> int:
    await ensure_admin_role()
    agent_key = os.environ["GEN9_AGENT_MODELS_KEY"]
    headers = {"Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}"}
    async with httpx.AsyncClient(
        base_url=os.environ["LITELLM_URL"], headers=headers, timeout=30
    ) as router:
        if (
            await router.get("/key/info", params={"key": agent_key})
        ).status_code == 200:
            print("gen9-agent's key exists")
        else:
            if not await retire(router, "gen9-agent"):
                return 1
            created = await router.post(
                "/key/generate",
                json={
                    "key": agent_key,
                    "key_alias": "gen9-agent",
                    "metadata": {"owner": "gen9-agent"},
                },
            )
            if created.status_code != 200:
                print(
                    f"could not create gen9-agent's key: HTTP {created.status_code}",
                    file=sys.stderr,
                )
                return 1
            print("gen9-agent's key created")

        # A key can name a default budget for its end users too (metadata end_user_budget_id), but in
        # v1.102.1 that one was stored and never enforced (explore/models/NOTES.md); the proxy-wide
        # default is. Clear the key's, which an earlier version of this script set.
        limits = agent_limits()
        before = (await router.get("/key/info", params={"key": agent_key})).json()
        before = before.get("info", {})
        update = {"key": agent_key, "metadata": {"owner": "gen9-agent"}, **limits}
        if before.get("max_budget") is None and limits["max_budget"] is not None:
            # A first daily budget counts from now: the key's lifetime spend would exceed it
            # at once. Only then; a later start keeps the day's spend, or a restart would reset it
            print(
                f"gen9-agent's key: lifetime spend ${before.get('spend') or 0:.4f}, counted from now"
            )
            update["spend"] = 0.0
        await router.post("/key/update", json=update)
        info = (await router.get("/key/info", params={"key": agent_key})).json()
        held = {k: info.get("info", {}).get(k) for k in limits}
        if held != limits:
            print(f"gen9-agent's key has {held}, not {limits}", file=sys.stderr)
            return 1
        print(
            f"gen9-agent's key: at most ${limits['max_budget']} a day"
            if limits["max_budget"] is not None
            else "gen9-agent's key: no daily budget (GEN9_AGENT_BUDGET_USD is empty)"
        )
        if not await ensure_scoped_key(
            router,
            os.environ["GEN9_AGENT_API_MODELS_KEY"],
            API_KEY_ALIAS,
            API_KEY_SCOPE,
            "may only embed and rerank",
        ):
            return 1
        # The evals' judge (gen9-agent/evals): the chat alias only, within a daily budget. An
        # install from before it has none until make setup STACKS=models adds it
        evals_key = os.environ.get("GEN9_EVALS_MODELS_KEY", "").strip()
        if evals_key:
            if not await ensure_scoped_key(
                router,
                evals_key,
                EVALS_KEY_ALIAS,
                EVALS_KEY_SCOPE,
                "may only call chat, within its daily budget",
                evals_limits(),
            ):
                return 1
        else:
            print(
                "no GEN9_EVALS_MODELS_KEY: make setup STACKS=models adds the evals' key"
            )

        budget = budget_from_env()
        # /budget/info answers 200 with an empty list for a budget that doesn't exist
        info = await router.post("/budget/info", json={"budgets": [BUDGET_ID]})
        exists = info.status_code == 200 and bool(info.json())
        response = await router.post(
            "/budget/update" if exists else "/budget/new", json=budget
        )
        if response.status_code != 200:
            print(
                f"could not {'update' if exists else 'create'} the users' budget: HTTP {response.status_code} {response.text[:200]}",
                file=sys.stderr,
            )
            return 1
        limits = [
            f"${budget['max_budget']} per {budget['budget_duration'] or 'ever'}"
            if budget["max_budget"] is not None
            else "",
            f"{budget['rpm_limit']} requests a minute" if budget["rpm_limit"] else "",
        ]
        print(
            "each user's default limit:",
            ", ".join(limit for limit in limits if limit) or "none",
        )
        return 0


sys.exit(asyncio.run(main()))
