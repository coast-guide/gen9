"""Re-embedding with an untyped `vector` column and per-model partial HNSW expression indexes
(pgvector README v0.8.6, "different dimensions in the same column"). Questions:
1. Does the planner use the partial index when the model name is a bound parameter, including
   after psycopg starts preparing statements (prepare_threshold 5) and Postgres may pick a
   generic plan?
2. Index build time at volume (per user filtering too, with iterative scans).
3. Search during re-embedding: old model's rows via the old index while new rows fill in.
Run: docker compose -f explore/search/reembed/compose.yaml up -d --wait, then from gen9-agent/:
  uv run python explore/search/reembed/probe.py (then probe2.py, on the data it leaves)
"""

import asyncio
import random
import time

import psycopg

DSN = "postgresql://postgres:exp@127.0.0.1:19093/postgres"
N = 20_000
A, B = "openai/text-embedding-3-small", "ollama/embeddinggemma"


def vec(n: int) -> str:
    v = [random.gauss(0, 1) for _ in range(n)]
    norm = sum(x * x for x in v) ** 0.5
    return "[" + ",".join(f"{x / norm:.5f}" for x in v) + "]"


async def plan(conn, sql, params) -> str:
    rows = await (await conn.execute("explain " + sql, params)).fetchall()
    text = " ".join(r[0].strip() for r in rows)
    for name in ("chat_search_hnsw_a", "chat_search_hnsw_b"):
        if name in text:
            return f"index {name}"
    return "no vector index: " + text[:120]


async def main() -> None:
    random.seed(1)
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        await conn.execute("create extension if not exists vector")
        await conn.execute(
            "create table chat_search (id bigserial primary key, user_id int not null, "
            "embed_model text, embedding vector)"
        )
        await conn.execute("create index on chat_search (user_id)")
        t = time.monotonic()
        async with (
            conn.cursor() as cur,
            cur.copy(
                "copy chat_search (user_id, embed_model, embedding) from stdin"
            ) as copy,
        ):
            for i in range(N):
                await copy.write_row((i % 200, A, vec(1536)))
        print(f"loaded {N} rows of model A (1536) in {time.monotonic() - t:.0f}s")
        t = time.monotonic()
        await conn.execute(
            f"create index chat_search_hnsw_a on chat_search using hnsw "
            f"((embedding::vector(1536)) vector_cosine_ops) where embed_model = '{A}'"
        )
        print(f"1. index A built in {time.monotonic() - t:.1f}s")
        await conn.execute("analyze chat_search")

        q = vec(1536)
        sql = (
            "select id from chat_search where user_id = %s and embed_model = %s "
            "order by embedding::vector(1536) <=> %s::vector(1536) limit 10"
        )
        print("   bound model, first execution:", await plan(conn, sql, (7, A, q)))
        # Run it enough times for psycopg to prepare it, then look at the plan Postgres keeps
        for _ in range(8):
            await (await conn.execute(sql, (7, A, q))).fetchall()
        rows = await (
            await conn.execute(
                "select generic_plans, custom_plans from pg_prepared_statements"
            )
        ).fetchall()
        print("   prepared statements (generic, custom plans):", rows)
        # Force the generic plan to see whether it can use the partial index at all
        await conn.execute("set plan_cache_mode = force_generic_plan")
        await conn.execute(
            "prepare s(int, text, vector) as "
            + sql.replace("%s", "$1", 1).replace("%s", "$2", 1).replace("%s", "$3", 1)
        )
        rows = await (
            await conn.execute(f"explain execute s(7, '{A}', '{q}')")
        ).fetchall()
        text = " ".join(r[0].strip() for r in rows)
        print(
            "   generic plan:",
            "uses index A"
            if "chat_search_hnsw_a" in text
            else "no index: " + text[:140],
        )
        await conn.execute("deallocate s")
        await conn.execute("set plan_cache_mode = auto")

        # 3. Re-embedding: half the rows move to model B (768) while A's index still serves A
        t = time.monotonic()
        await conn.execute(
            f"create index chat_search_hnsw_b on chat_search using hnsw "
            f"((embedding::vector(768)) vector_cosine_ops) where embed_model = '{B}'"
        )
        print(f"3. empty index B built in {time.monotonic() - t:.2f}s")
        t = time.monotonic()
        ids = [
            r[0]
            for r in await (
                await conn.execute("select id from chat_search where id % 2 = 0")
            ).fetchall()
        ]
        async with conn.cursor() as cur:
            for chunk in range(0, len(ids), 500):
                await cur.executemany(
                    "update chat_search set embed_model = %s, embedding = %s::vector where id = %s",
                    [(B, vec(768), i) for i in ids[chunk : chunk + 500]],
                )
        print(
            f"   re-embedded {len(ids)} rows to B in {time.monotonic() - t:.0f}s (index B maintained)"
        )
        await conn.execute("analyze chat_search")
        await conn.execute("set hnsw.iterative_scan = relaxed_order")
        sql_b = sql.replace("1536", "768")
        qb = vec(768)
        print("   B query:", await plan(conn, sql_b, (7, B, qb)))
        print("   A query still:", await plan(conn, sql, (7, A, q)))
        got = await (await conn.execute(sql_b, (7, B, qb))).fetchall()
        print(
            f"   B results for user 7: {len(got)} (user has {N // 200 // 2} rows of B)"
        )


asyncio.run(main())
