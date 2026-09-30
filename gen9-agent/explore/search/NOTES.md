# Search in Postgres: experiment

For the plan's "Search in gen9-postgres". It ran outside the repository: a throwaway image and
container (`exp-pgsearch`, port 19092). Embeddings came from gen9-models' `embed` alias
(text-embedding-3-small, 1536 dimensions).

**Setup.**
- The same pinned image as gen9-postgres (`pgvector/pgvector:0.8.6-pg18-trixie`), plus
  pg_textsearch 1.4.0. Its release zip for pg18 arm64 holds one Debian package,
  `pg-textsearch-postgresql-18_1.4.0-1_arm64.deb` (654 KB), installed with `dpkg -i`.
- `create extension pg_textsearch` fails unless the library is preloaded: start Postgres with
  `-c shared_preload_libraries=pg_textsearch` (a restart setting).
- Extensions: vector 0.8.6, pg_textsearch 1.4.0, pg_trgm 1.6.

**Data.** 12 real chats for two users (`alice`, `bob`), and 3,000 filler rows for 50 other users
(random unit vectors, generic words). The table is `chat_search(user_id, title, body,
embedding vector(1536))` with four indexes:
- BM25 on `body` (`text_config='english'`);
- HNSW on `embedding` (cosine);
- GIN trigram on `title`;
- btree on `user_id`.

All four were built in 5.4 s.

| Kind | Query (alice) | Result |
| --- | --- | --- |
| BM25 | "autovacuum scale factor" | "Postgres vacuum tuning" first |
| Fuzzy (`title % q`, `similarity`) | "sourdugh startr" | "Sourdough starter", similarity 0.52 |
| Vector (`<=>`, `hnsw.iterative_scan = relaxed_order`) | "how do I make my background jobs try again after a failure" (no shared words) | "Temporal workflow retries" first (distance 0.577; next 0.795) |
| Hybrid (RRF, k 60, top 20 of each) | "retry failed activity" | "Temporal workflow retries" first, in 5 ms |
| Isolation | bob searching alice's topics | 0 rows of any other user |

What it showed about BM25:
- **With bound parameters, name the index:** `body <@> to_bm25query($1, 'chat_search_bm25')`.
  Otherwise: "no BM25 index found for text <@> text expression".
- **`<@>` scores rows that don't match** too, with 0, and they pad the results (bob's query
  returned his three unrelated chats). Filter them with `(body <@> q) < 0`, or with
  `body @@ websearch_to_tsquery('english', $1)`. Both left only the matching chat.
- **Without a user filter,** the plan is `Index Scan using chat_search_bm25 … Order By: (body <@>
  …bm25query)` with the `@@` filter. With the per-user filter and 8 rows per user, the planner
  chose the `user_id` index and sorted exactly, for both BM25 and vector. That is right at
  per-user scale; the BM25 and HNSW indexes matter once one user has many rows.

For the build: preload pg_textsearch in gen9-postgres, name the BM25 index in queries, filter out
non-matches before fusing, and filter every query by the user.

## Re-embedding when `embed` changes: probe

Question: can `chat_search` hold embeddings from more than one model and dimension, so that
changing `embed` needs no migration? pgvector's README (v0.8.6, "Can I store vectors with
different dimensions in the same column?") says to use an untyped `vector` column, with one index
per dimension: an expression index, `(embedding::vector(n))`, made partial with `WHERE` on the
model.

Setup: `reembed/` (a throwaway Compose project, `exp-reembed`, port 19093, tmpfs) on the
gen9-postgres image. 20,000 rows of random unit vectors for 200 users, with model A at 1536
dimensions. Then half the rows were moved to model B at 768.

What it showed:
- **The planner uses the partial index when the model is a bound parameter** under a custom plan,
  as with a literal. A forced generic plan (`plan_cache_mode = force_generic_plan`) can't prove
  the `WHERE`, so it sorts every row of the model.
- **In practice Postgres kept custom plans.** After psycopg began preparing the statement (it
  prepares after 5 executions), 30 runs gave 0 generic and 25 custom plans: the generic plan
  costs far more. 8.3 ms a query at 10,000 rows of the model, including making the query vector.
- **Re-embedding with both indexes:** the empty index for B built in 0.01 s. 10,000 rows moved to
  B while it was maintained took 81 s, including making the vectors in Python. Then B's queries
  used B's index and A's used A's.
- **Index builds need memory.** 20,000 rows of 1536 dimensions (about 120 MB) took 195.7 s with
  the default `maintenance_work_mem` (64 MB); pgvector noticed "hnsw graph no longer fits into
  maintenance_work_mem after 9750 tuples". With 1 GB it took 52.9 s. At 10,000 rows, which nearly
  fit, it was 26.0 s against 22.9 s, and 2 parallel workers made no difference (22.9 s against
  22.8 s).
- **Per user:** a user with 100 rows got the `user_id` index and an exact sort, which is right. A
  user with 9,000 rows got A's HNSW index, and iterative scans filled all 10 results.

For the build (search unit 3):
- `embedding` becomes an untyped `vector`, with one partial HNSW index per model.
- Search queries cast to the model's dimension and filter on the current model. They set
  `plan_cache_mode = force_custom_plan` locally, so a generic plan can never skip the index.
- Re-embedding creates the new model's index first, empty, with `CREATE INDEX CONCURRENTLY`. It
  sets `maintenance_work_mem` for that session, and drops the old model's index once no row uses
  it.

## A self-hosted reranker on this machine: TEI

Setup: a throwaway Compose project (`exp-rerank`) with TEI 1.9.4's native arm64 CPU image
(`cpu-arm64-1.9.4`, pinned by digest), in Docker Desktop's VM (9.7 GiB, about 4.3 GiB available
with every Gen9 stack up, swap full). One server per model, capped with `mem_limit`.

What it showed:
- **Two servers at once were both OOM-killed** (exit 137, `OOMKilled=true`) while downloading and
  warming up. No Gen9 container was touched (none killed or restarted).
- **`Alibaba-NLP/gte-reranker-modernbert-base` (150M) was OOM-killed in warmup under 3 GiB,**
  even with `--max-batch-tokens 8192`. Its window is 8,192 tokens, and TEI warms up at the
  model's full length. Full attention at that length in fp32 is about 3.2 GB for one layer. TEI
  has no option to serve a shorter input length than the model's.
- **`BAAI/bge-reranker-base` (278M, 512 tokens) was OOM-killed under 3 GiB while loading its ONNX
  weights (1.1 GB),** after the download and before warmup, with the weights already cached (peak
  sample 1.95 GiB; the samples come 1 s apart and miss the spike).
- The download itself held about 2.3 GiB of the container's memory.

For the plan: TEI on CPU needs more than 3 GiB for these rerankers here. So a self-hosted reranker
belongs on a host with memory to spare, or behind a hosted API.

## A self-hosted reranker on this machine: llama.cpp

llama.cpp's server (MIT; `ghcr.io/ggml-org/llama.cpp:server`, linux/amd64 and linux/arm64, pinned
by digest `sha256:fffcc1af…5edf`) serves `/v1/rerank` with `--reranking`. It takes `{query,
documents, top_n}` and answers `results[{index, relevance_score}]` (tools/server/README.md,
"POST /reranking", "similar to Jina's"). LiteLLM's `hosted_vllm` rerank provider sends that shape
to `{api_base}/rerank` (`litellm/llms/hosted_vllm/rerank/transformation.py`). Models were
ggml-org's own conversions, one server at a time, capped at 1.5 GiB. The test used 20
candidates of chat length, as hybrid search's top 20 would give.

| Model (GGUF, Apache-2.0) | File | Memory | 20 candidates | Ranking |
| --- | --- | --- | --- | --- |
| `ggml-org/jina-reranker-v1-turbo-en-GGUF` (38M, F16) | 77 MB | 49 MiB loaded, 175 MiB serving | median 897 ms, max 1,358 ms | right chat first for 3 of 3 paraphrased queries |
| `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` | 639 MB | OOM-killed under 1.5 GiB | n/a | n/a |

- **Jina turbo's scores are compressed and uncalibrated:** the right chat scored -0.087, and an
  unrelated query's best was -0.097. It orders well but gives no usable cut-off.
- **Qwen3-Reranker-0.6B first sized its context at 76,544 tokens,** above its trained 40,960, and
  was OOM-killed while loading. With `--ctx-size 8192 --batch-size 2048 --ubatch-size 2048` it
  loaded (peak 1.44 GiB), then was OOM-killed on the first request: 4 slots of 8,192 tokens.

For the plan: rerank stays optional, through the router's `rerank` alias. The light self-hosted
choice for the `local` profile is llama.cpp with Jina turbo. Larger rerankers need a host with
memory to spare, or a hosted API.

## The reranker on real chats, through Gen9

Setup: gen9-models' `local` profile serving `rerank` (llama.cpp b11151, Jina turbo), and
gen9-agent with `SEARCH_RERANK=true`. The data was alan's three chats (Postgres autovacuum,
Temporal retries, a houseplant for low light), and 7 queries in keyword, semantic and hybrid
modes, before and after.
- **It reranks.** Every result list of more than one hit came back `ranked_by: rerank`. Fuzzy, and
  lists of one hit, were left alone.
- **It didn't help.** No query's first result got better.
  - For "my plant keeps failing, should I retry", hybrid had the Temporal chat first before and
    after (the plant chat is the right one).
  - In semantic mode, the reranker moved the plant chat from first to second.
  - The other first results were unchanged, and some lower ranks were reordered.
- **Latency:** 0.1–0.4 s added. Keyword with rerank answered in 0.09–0.24 s, hybrid in 0.5–0.8 s
  (embedding included).
- **A downed reranker cost 15–26 s:** with `reranker` stopped, the router retried
  (`num_retries: 2`) before answering 500. gen9-agent now bounds the call at 3 s
  (`asyncio.timeout`). Measured after: 3.4 s keyword and 4.1 s hybrid with the reranker down,
  results in the search's own order.
- **llama.cpp needs `--model` with `--model-url`.** With only `--model-url`, b11151 started in
  "router mode" with no model: "models will be automatically loaded on-demand", and it warns
  "do not expose to untrusted environments". It answered 400 "model … not found" until given a
  path and an `--alias`.
- **Changing `config.yaml` alone doesn't restart the router.** `docker compose up -d` recreates
  it for `.env` changes but not for a bind-mounted file, nor for Compose `configs:` from a file
  (tested in a throwaway project). It needs `docker compose restart litellm`.

For the build: rerank stays off by default. The small reranker exercises the path; a useful one
needs a larger or hosted model.

## Meaning search with Qwen3-Embedding: its query form and a floor

Question: why did a search by meaning offer chats that aren't about the query? A person with two
chats searched "lighthouses" and got both "by meaning", neither about lighthouses, and for
"numbers" the chat "Reply with one word: hi" came first (docs/plans/gen9-learn.md, M9, F7).
`embed` is now Qwen3-Embedding-8B, not the models the 60% share was measured with.

Setup: `relevance/probe.py`, inside `gen9-agent-api` through the router. 20 made-up chats as
`chat_search` holds them (question, blank line, answer; one a two-word greeting), 20 queries that
paraphrase one chat each (German and Spanish among them) and 14 that no chat answers. Nothing is
written. Cosine similarity, as search computes it.

| | Query as typed | Query in the model's form |
| --- | --- | --- |
| Right chat first | 16 of 20 | 20 of 20 |
| Right chat's score | from 0.368 | from 0.437 (German 0.442, Spanish 0.520) |
| Best wrong chat | up to 0.562 | up to 0.419 |
| No chat answers (real topics) | best up to 0.498 | best up to 0.334 |
| Wrong chats the 60% share kept | 156 | 129 |

What it showed:
- **Short chats pull every query typed as is:** the greeting chat was nearest for all 14 queries
  no chat answered (0.43 to 0.50 for the 11 real topics), as "Leuchttürme" ranked "Reply with one word: over" first
  in gen9-learn's run. The model card says so in its terms: queries need an instruction
  ("Instruct: {task}\nQuery: {query}"), documents none, and without one retrieval drops "by
  approximately 1% to 5%".
- **With the instruction the scores separate:** every right chat above every wrong one, and a
  floor of 0.40 keeps all 20 right chats while dropping every query no chat answers.
- **The share alone can't say "nothing":** the best hit is always kept, and when all scores are
  low 60% of the best admits almost everything (16 of 20 chats for "recetas de paella").
- One-word queries stay ambiguous: "hi" finds the greeting (0.648, rightly), "test" too (0.556).

For the build: queries go in the model's form and matches must reach its floor, both looked up by
the model the router names, 0.40 for this model only (`api/search.py`, `QUERY_TEMPLATES`,
`SIMILARITY_FLOORS`); another model keeps the query as typed and the share alone until measured
here.
