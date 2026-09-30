"""Follow-up on the same throwaway data (probe.py left 10k rows of A at 1536, 10k of B at 768).
a. No user filter: does the planner use the partial expression index when the model is a literal,
   a bound parameter (custom plan), and a generic plan?
b. psycopg prepares after 5 executions; after many more, does Postgres switch to a generic plan,
   and does that plan still use the index?
c. Build time of index A with maintenance_work_mem 1GB and 2 parallel workers vs the default.
d. One heavy user (most rows): per-user query with iterative scans uses the index and fills 10.
"""

import asyncio
import random
import time

import psycopg

DSN = "postgresql://postgres:exp@127.0.0.1:19093/postgres"
A, B = "openai/text-embedding-3-small", "ollama/embeddinggemma"


def vec(n: int) -> str:
    v = [random.gauss(0, 1) for _ in range(n)]
    norm = sum(x * x for x in v) ** 0.5
    return "[" + ",".join(f"{x / norm:.5f}" for x in v) + "]"


def uses(rows) -> str:
    text = " ".join(r[0].strip() for r in rows)
    for name in ("chat_search_hnsw_a", "chat_search_hnsw_b"):
        if name in text:
            return f"index {name[-1].upper()}"
    return "no vector index (" + text[:90] + ")"


async def main() -> None:
    random.seed(2)
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        q = vec(1536)
        lit = f"select id from chat_search where embed_model = '{A}' order by embedding::vector(1536) <=> %s::vector(1536) limit 10"
        print(
            "a. literal model:",
            uses(await (await conn.execute("explain " + lit, (q,))).fetchall()),
        )
        bound = "select id from chat_search where embed_model = %s order by embedding::vector(1536) <=> %s::vector(1536) limit 10"
        print(
            "   bound model:",
            uses(await (await conn.execute("explain " + bound, (A, q))).fetchall()),
        )
        await conn.execute("set plan_cache_mode = force_generic_plan")
        await conn.execute(
            "prepare g(text, vector) as select id from chat_search where embed_model = $1 "
            "order by embedding::vector(1536) <=> $2::vector(1536) limit 10"
        )
        print(
            "   generic plan, bound model:",
            uses(
                await (
                    await conn.execute(f"explain execute g('{A}', '{q}')")
                ).fetchall()
            ),
        )
        await conn.execute("deallocate g")
        await conn.execute("set plan_cache_mode = auto")

        # b. Let psycopg prepare it and Postgres decide
        for _ in range(30):
            await (await conn.execute(bound, (A, vec(1536)))).fetchall()
        row = await (
            await conn.execute(
                "select generic_plans, custom_plans from pg_prepared_statements where statement like '%embed_model = $1%'"
            )
        ).fetchone()
        print("b. after 30 runs through psycopg (generic, custom plans):", row)
        t = time.monotonic()
        for _ in range(20):
            await (await conn.execute(bound, (A, vec(1536)))).fetchall()
        print(
            f"   20 more runs: {(time.monotonic() - t) / 20 * 1000:.1f} ms each (includes making the query vector)"
        )

        # c. Rebuild A with more memory and parallel workers
        await conn.execute("drop index chat_search_hnsw_a")
        await conn.execute("set maintenance_work_mem = '1GB'")
        await conn.execute("set max_parallel_maintenance_workers = 2")
        t = time.monotonic()
        await conn.execute(
            f"create index chat_search_hnsw_a on chat_search using hnsw "
            f"((embedding::vector(1536)) vector_cosine_ops) where embed_model = '{A}'"
        )
        print(
            f"c. index A over 10k rows, 1GB and 2 workers: {time.monotonic() - t:.1f}s"
        )
        await conn.execute("reset maintenance_work_mem")
        await conn.execute("reset max_parallel_maintenance_workers")

        # d. A heavy user: move most of A's rows to user 1
        await conn.execute(
            f"update chat_search set user_id = 1 where embed_model = '{A}' and id % 10 <> 0"
        )
        await conn.execute("analyze chat_search")
        await conn.execute("set hnsw.iterative_scan = relaxed_order")
        per_user = "select id from chat_search where user_id = %s and embed_model = %s order by embedding::vector(1536) <=> %s::vector(1536) limit 10"
        print(
            "d. heavy user:",
            uses(
                await (await conn.execute("explain " + per_user, (1, A, q))).fetchall()
            ),
        )
        got = await (await conn.execute(per_user, (1, A, q))).fetchall()
        print(f"   rows: {len(got)}")
        print(
            "   light user:",
            uses(
                await (await conn.execute("explain " + per_user, (7, A, q))).fetchall()
            ),
        )


asyncio.run(main())
