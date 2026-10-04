# gen9-models

The model router: every model Gen9 uses, of every kind and wherever it is hosted, reached through
one OpenAI-compatible API by **alias**. It runs [LiteLLM Proxy](https://github.com/BerriAI/litellm)
v1.103.1 (MIT) with a Postgres of its own for keys, budgets and spend. gen9-agent asks for `chat`
or `embed`, never for a vendor's model. Which model answers is decided here, in `config.yaml`,
and provider keys never leave this stack. Why LiteLLM: the Decision Log in
[docs/plans/harness.md](../docs/plans/harness.md).

| Service | What |
| --- | --- |
| `postgres` | Postgres 16, used by LiteLLM only: virtual keys, budgets, spend logs |
| `litellm` | The router, on port 4000 |
| `keys` | One-shot job: gen9-agent's virtual key, every user's default budget, the admin API's database role (`scripts/ensure-keys.py`) |
| `admin` | Gen9's admin API for the router, on port 4001: erasing a user's records (`admin/app.py`) |
| `searxng` | Self-hosted web search behind the router's search alias `web` (SearXNG, AGPL-3.0, its own service) |
| `ollama`, `ollama-pull` | Only with the `local` profile: self-hosted models (Ollama) and the job that pulls them |
| `reranker` | Only with the `local` profile: the `rerank` alias, llama.cpp's server with Jina's turbo reranker |
| `ready` | Idles, so `docker compose up --wait` waits for `keys`, `admin` and `searxng` (and `ollama-pull` with the `local` profile) |

**Requires:** Docker with Compose v2, bash, OpenSSL (or `od`), and a key for at least one
provider.

## Quick start

```bash
./init-env.sh --openai-key-from ../gen9-agent/.env --agent-env-file ../gen9-agent/models.local.env \
  --agent-api-env-file ../gen9-agent/models-api.local.env --evals-env-file ../gen9-agent/models-evals.local.env
docker network inspect gen9-models >/dev/null 2>&1 || docker network create gen9-models   # once; make up does it
docker compose up -d --wait
```

`make setup STACKS=models` and `make up` do the same; `make setup` also asks for `OPENROUTER_API_KEY` and `OPENAI_API_KEY` (hidden), or takes them from the environment, and `init-env.sh --keys-later` leaves them to it. The first start takes a minute or two: the
image is published for `linux/amd64` only, so on Apple silicon it runs under emulation.

The API is at `http://127.0.0.1:19000/v1`; other stacks' containers use `http://gen9-models:4000/v1`.
Every call needs a key. gen9-agent's worker has one for every model (`gen9-agent/models.local.env`),
within `GEN9_AGENT_BUDGET_USD` a day (default 5, Budgets);
its API has one that may only embed and rerank (`gen9-agent/models-api.local.env`, Security).
`make evals` has one that may only call `chat`, for its judge, within `GEN9_EVALS_BUDGET_USD` a
day (default 5; `gen9-agent/models-evals.local.env`). `scripts/ensure-keys.py` sets both scopes
and the budget on every start, then reads them back.

## Models (`config.yaml`)

| Alias | Kind | Today |
| --- | --- | --- |
| `chat` | the agent's model: reasoning, tools, images | OpenAI GPT-6 Luna through OpenRouter, falling back to `chat-backup` |
| `chat-backup` | used when `chat` keeps failing | DeepSeek-V4.1-Flash (open weights, MIT) through OpenRouter |
| `vision` | images as input | inclusionAI Ling 3.0 Flash VL through OpenRouter |
| `embed` | text embeddings, 1024 dimensions | Qwen3-Embedding-8B (Apache-2.0) through OpenRouter, shortened to 1024 dimensions |
| `speak` | text to speech | OpenAI `gpt-4o-mini-tts` |
| `transcribe` | speech to text | OpenAI `gpt-4o-mini-transcribe-2025-12-15`, $0.003 a minute (ask for `response_format=json`) |
| `image` | image generation | OpenAI `gpt-image-1-mini`, $8 per million output tokens |
| `rerank` | reordering search results (gen9-agent's `SEARCH_RERANK`, off by default) | with the `local` profile: Jina's turbo reranker on llama.cpp ([Self-hosted models](#self-hosted-models)) |

Why these:
- **`chat`:** GPT-6 Luna (released 2026-09-22) is OpenAI's newest and its cheapest current
  model: $0.10 per million input tokens and $0.50 per million output. It has a 1M-token context,
  and reasons while it calls tools. Only OpenAI, Azure and Bedrock serve it.
- **`vision`:** Ling 3.0 Flash VL ($0.021 and $0.062). Five test images, each
  three times through OpenRouter under Gen9's `data_collection: deny`: an all-red picture, a red
  square on white, an invoice's text, a bar chart's tallest bar and its colour, a button's colour.
  It read all 15 right, as GPT-5.4 nano ($0.20 and $1.25, the alias before) did, at a third of
  nano's cost per call. GPT-6 Luna read the text, the chart and the button right, but called the
  red square “White”, “Gray” or “Lavender” 6 times in 6 (an all-red picture
  “Blue”). A person's attached image goes to `chat`, not here: Luna reads what people attach
  (screenshots, charts, text) right in these tests, and flat blocks of colour wrong.
- **`chat-backup`:** another company's model, served by other providers, so a fallback also
  covers an OpenAI outage. It reads images too (a red square: "Red", in 2 s), so a turn with an
  attached image falls back as well.
- **`embed`:** Qwen3-Embedding-8B costs $0.01 per million tokens, against $0.02 for OpenAI's
  `text-embedding-3-small`.
- **Search:** with `chat` behind OpenRouter, gen9-agent searches the web through this router
  (`WEB_SEARCH=router` in gen9-agent's `.env`). OpenAI's built-in search works only on OpenAI's
  own API.

How OpenRouter picks a provider for each OpenRouter alias (`x-openrouter` in `config.yaml`, sent
as its [`provider` object](https://openrouter.ai/docs/guides/routing/provider-selection)):
- **Data:** `data_collection: deny`: never a provider that trains on prompts. For zero data
  retention add `zdr: true`; GPT-6 Luna then runs only on Azure. OpenAI keeps prompts for abuse
  monitoring and doesn't train on them.
- **Otherwise OpenRouter's defaults:**
  - the cheaper providers first;
  - for calls with tools, [Auto Exacto](https://openrouter.ai/docs/guides/routing/auto-exacto)'s
    ordering by tool-calling success;
  - the next provider when one fails.
- **Not `require_parameters: true`:** no GPT-6 Luna endpoint lists `temperature` or
  `parallel_tool_calls`, so every call fails with 404.
- **Cost:** the router records OpenRouter's own cost for each chat call. `embed` has its price in
  `config.yaml`, which LiteLLM doesn't know for that model; without it, embeddings would cost
  nothing in anyone's budget.
- **Still on OpenAI's key:** `speak`, `transcribe` and `image`.
  - OpenRouter serves no OpenAI speech model.
  - Its transcription API takes base64 JSON, not OpenAI's form upload.
  - It takes `gpt-image-2` only on its own images API, which this LiteLLM version doesn't call.

To serve an alias with another model or provider:
1. Change its `litellm_params` in `config.yaml`, or add a second deployment under the same
   `model_name`. The file has examples for OpenAI, Anthropic, Gemini, Bedrock, Azure, vLLM and
   Ollama, and OpenRouter reaches most of them with one key.
2. Put the provider's key in `.env`, then run `docker compose up -d litellm`. It recreates the
   container: `docker compose restart` keeps the old environment, so a new key doesn't arrive.
3. For a change to `config.yaml` alone, run `docker compose up -d --force-recreate litellm`. The
   router reads the file only when it starts, and `up` alone doesn't restart it for that file.
   The file is mounted on its own, so after a save that replaces it (git, `sed -i`, many
   editors) a container keeps the old one through a restart: a new container reads the new
   one.

gen9-agent needs no change. Several deployments of one alias share its traffic, and a failing one
is skipped.

> [!NOTE]
> gen9-agent searches the web through this router by default (`WEB_SEARCH=router`; Search,
> below), so `chat` can be any provider's model. `WEB_SEARCH=model` uses OpenAI's built-in search
> instead, which also opens pages, and needs `chat` on OpenAI's own API.

The router retries a failed call twice, then tries the alias's fallbacks, before gen9-agent (and
Temporal) see an error.

A call no model could take falls back all the same: LiteLLM's `fallbacks` take every error, 400s
included, and 1.103.0 has no setting to leave some out (only `disable_fallbacks` per request or
key, which would also give up the fallback in an outage). An attached image that can't be decoded
was refused by `chat` at once and by `chat-backup` 59 s later; the agent then read on without it
(gen9-agent's deepagents 0.7.19). Rare, costs nothing, and kept for the fallback's sake.

## Self-hosted models

Models can run on this machine instead of a provider, behind the same aliases. To turn it on, add
`local` to `COMPOSE_PROFILES` in `.env` (and the line below if you want other models), then run
`make up STACKS=models`:

```bash
COMPOSE_PROFILES=postgres,local                     # local starts ollama and the reranker, pulls the models
GEN9_LOCAL_MODELS=qwen3:0.6b embeddinggemma         # optional; these are the defaults
```

That serves:
- `chat-local` (qwen3 0.6B, tool calls) and `embed-local` (embeddinggemma, 768 dimensions), on
  Ollama;
- `rerank`: Jina's turbo reranker (38M, English, Apache-2.0) on llama.cpp's server (MIT), pinned
  as tag@digest, the model by its repository's commit. It uses about 50 MiB loaded and 175 MiB
  serving (gen9-agent/explore/search/NOTES.md).

To turn it off, remove `COMPOSE_PROFILES` from `.env` and run `make down STACKS=models`, then
`make up STACKS=models`: `make up` alone leaves Ollama and the reranker running, as Compose
doesn't touch the services of a profile it isn't given. The downloaded models (about 1.1 GB) stay
in their volumes, and `make wipe` keeps them too ([docs/operations.md, "Start over"](../docs/operations.md#start-over)).

To serve an existing alias locally, point it at Ollama in `config.yaml`, for example `embed` at
`{model: ollama/embeddinggemma, api_base: "http://ollama:11434"}`. Then run
`docker compose up -d --force-recreate litellm`; gen9-agent needs no change.

Things to know:
- A different embedding model means different vectors (and dimensions). gen9-agent re-embeds its
  past chats by itself within 15 minutes (its `reindex-search` Schedule; gen9-agent/README.md,
  "Search"). Its search asks Qwen3-Embedding in that model's query form and keeps matches from
  0.40 (gen9-agent/README.md, "Search", "The query in the model's form"); another model is asked
  as typed with no floor, until its row is added there.
- Ollama runs on CPU here: a small model's first call took 30 s, loading included (13 s, most of it qwen3's thinking). It uses about 1.7 GiB with both models loaded. A GPU host runs Ollama, or vLLM, as its own service instead
  (`hosted_vllm/…`).
- `chat` itself needs OpenAI while `WEB_SEARCH=model` (the default). Use `WEB_SEARCH=router` to
  serve it locally.
- Ollama 0.34.4, the latest release, is built with x/crypto 0.43.0 and Go 1.26.0,
  which Docker Scout reports (7 critical) and `govulncheck` confirms. The critical ones are in
  x/crypto's SSH server code (GO-2026-6354, -6355, -6303); Ollama uses SSH keys only to sign its
  requests to ollama.com, and serves no SSH. It listens only on gen9-models' own network.
- Embeddings through Ollama need `drop_params: true` (set): the OpenAI SDK always sends
  `encoding_format`, which the Ollama provider refuses.
- **The reranker is small, and it shows.** It was chosen because larger rerankers ran out of
  memory on a laptop (gen9-agent/explore/search/NOTES.md). On real chats it reordered results but
  never improved the first one, and made one worse. That is why gen9-agent leaves
  `SEARCH_RERANK` off by default. For better reranking, point `rerank` at a larger model on a
  host with memory, or at a hosted one (`cohere/rerank-v3.5`, `voyage/…`, `jina_ai/…`) with its
  key in `.env`.
- llama.cpp's server needs a model path (`--model`). Without one it starts in "router mode",
  loading models on demand, which it warns against for untrusted callers.

## Search

Web search is an alias too, `web`, served by the router's search API
(`POST /v1/search/web`, Perplexity's Search API shape). gen9-agent's `web_search` tool uses it when
`WEB_SEARCH=router`, the default. Any of the 18 providers LiteLLM supports can serve `web`, so
search is switched here, like a model, with no change in gen9-agent.
- **Default:** a self-hosted SearXNG in this stack, with no key and no account. Only the router
  reaches it (no host port, this stack's network only).
  - Its engines are the ones that answered from here: Google's scraper, Yahoo and
    Bing, added to its defaults, with Wikidata dropped (`searxng/settings.yml`). With the
    defaults alone, 15 searches in 15 came back empty; with these, none did.
  - Scraped engines still block bursts of searches, and SearXNG then suspends them for an hour
    or more.
  - They also answer with junk at times: a question about an RFC came back with
    adult sites and unrelated help pages among its results (the Bing scraper's known fault,
    searxng/searxng#6671). So SearXNG filters adult results strictly (`safe_search: 2`; its
    default is none, and LiteLLM's SearXNG provider sends no setting of its own), and gen9-agent
    keeps only results that share words with the query (gen9-agent/README.md, "Models").
- **Hosted instead,** for search that holds up under load: set `search_provider` in
  `config.yaml` to `brave`, `tavily`, `exa_ai` or another LiteLLM supports, and put its key in
  `.env`. `config.yaml` shows how to keep SearXNG as its fallback when it fails. A provider's
  empty answer isn't a failure, so it doesn't fall back. Brave, Exa and Tavily each include
  about 1,000 searches a month (their pricing pages).
- The router ignores `max_results` with SearXNG, so the tool keeps the first results itself (up
  to 10).
- LiteLLM's `duckduckgo` provider is left out on purpose: it calls DuckDuckGo's Instant Answer
  API, which returns topic pages, not web results (gen9-agent/explore/models/NOTES.md).

## Admin API

What LiteLLM's own API can't do with the access Gen9 gives its callers. It runs on LiteLLM's image
for its Python (FastAPI, uvicorn, psycopg 3), as `admin`, with no LiteLLM process.

| Endpoint | What |
| --- | --- |
| `POST /users/{sub}/erase` | Deletes everything the router keeps about one user: spend logs, daily totals, end-user row (usage and cost; no messages are stored). Returns how many rows went from each table. Safe to repeat |
| `GET /users/{sub}/budget` | One user's budget: `spent_usd` this period, `max_usd`, `period` and `resets_at` (UTC). A user without one of their own is on the default (`gen9-user-default`), whose reset time is everyone's; LiteLLM applies it without linking it to their row, and its reset job zeroes such rows with it. gen9-agent says when a person's limit resets from it (manual-e2e.md, P5-D1). Read with either of gen9-agent's keys |
| `GET /users/{sub}/usage` | What the router keeps about one user, day by day: requests, tokens and cost per model (no messages), for their data export (GDPR Art. 15). gen9-agent's API may call it with its own key (`GEN9_AGENT_API_MODELS_KEY`), which may do nothing else here |
| `GET /health` | Liveness |

A `{sub}` holding a control character (a NUL, which Postgres's text can't hold) or longer than
255 characters gets 422 before any query (it answered 500; manual-e2e.md, P6-B2).

`DeleteAccountWorkflow` calls it when an account is deleted, and again in its late passes, since
the router writes daily totals in batches (gen9-agent/README.md, "Deletion").

How it is locked down:
- **Callers** present gen9-agent's virtual key. It is compared in constant time with
  `GEN9_AGENT_MODELS_KEY`; anything else gets 401. It holds no master key.
- **Database.** It connects as `gen9_admin`, a role the `keys` job keeps in line
  (`GEN9_ADMIN_DB_PASSWORD`). That role may only read and delete `LiteLLM_SpendLogs`,
  `LiteLLM_DailyEndUserSpend` and `LiteLLM_EndUserTable`, and read `LiteLLM_BudgetTable`: it
  can't read keys or change anything else.
- **Reach.** Containers get it at `gen9-models-admin:4001` over `gen9-models`, which only
  gen9-agent joins, and this machine on 127.0.0.1:19001. gen9-agent's API is on that network too
  (search queries), but its key isn't the worker's, so it gets 401.

Why a service of Gen9's own:
- LiteLLM has no API that deletes spend logs.
- Deleting an end user needs the master key.
- A narrower key would need route restrictions, which are enterprise-only (docs/plans/harness.md,
  Decision Log).

## Files

| File | Purpose |
| --- | --- |
| `compose.yaml` | The services; the LiteLLM image pinned by digest |
| `config.yaml` | Aliases, fallbacks, retries |
| `scripts/ensure-keys.py` | gen9-agent's virtual keys (the API's scope set on every start), the default user budget, the admin API's role |
| `admin/app.py` | The admin API |
| `searxng/settings.yml` | SearXNG: defaults plus JSON answers, no rate limiter (only the router calls it) |
| `ruff.toml` | Lint rules for this stack's Python (as gen9-agent's: async-first) |
| `init-env.sh` | Generates `.env`, `../gen9-agent/models.local.env`, `../gen9-agent/models-api.local.env` and `../gen9-agent/models-evals.local.env`. See `--help` |
| `.env` | Generated, secret, gitignored: Postgres password, LiteLLM's master and salt keys, gen9-agent's keys, provider keys |

## Ports

| Port | Service | `.env` variable |
| --- | --- | --- |
| `19000` | The router's API (OpenAI-compatible) | `GEN9_MODELS_PORT` |
| `19001` | The admin API | `GEN9_MODELS_ADMIN_PORT` |

Postgres isn't published. Use `docker compose exec postgres psql -U litellm -d litellm`. It is a
profile `.env`'s `COMPOSE_PROFILES` lists; another server instead, by `LITELLM_DB_HOST`:
[docs/operations.md, "External services"](../docs/operations.md#external-services).

## Security

LiteLLM is widely used, and it has a record:
- PyPI releases 1.82.7 and 1.82.8 were backdoored on 2026-03-24, after a compromise of its CI. The
  Docker image was not affected.
- Advisories in 2026 include fixed bugs in its key checks. Most of the others are in features
  Gen9 doesn't use: the admin UI, SSO, MCP, guardrails and prompts.

So this stack:
- **Runs only the signed image, pinned by digest.** Before changing the digest, verify it (Upgrade).
- **Shuts features off:** the admin UI's sign-in (`DISABLE_ADMIN_UI`), the API docs (`NO_DOCS`,
  `NO_REDOC`), and models stored in the database (`STORE_MODEL_IN_DB=False`: models come only from
  `config.yaml`).
- **Doesn't fetch prices at start.** They come from the map inside the pinned image
  (`LITELLM_LOCAL_MODEL_COST_MAP`), not from GitHub's `main` branch.
- **Is reachable by few:** on 127.0.0.1 from this machine, and from containers only over
  `gen9-models`, which only gen9-agent joins. A container on any other stack's network can't
  connect, by name or by address.
- **Has three keys, each as narrow as its use.** The master key manages keys and never leaves this
  stack. gen9-agent's worker holds a virtual key that can call models (and the admin API). Its API
  holds one the router allows only the `embed` and `rerank` aliases: another model, a search tool
  or a management route gets 403 or 401 (`ensure-keys.py`, `API_KEY_SCOPE`; gen9-agent/explore/models/NOTES.md,
  "How narrow a virtual key can be"). Both are plain LiteLLM features, no enterprise license.
- **Keeps provider keys here.** They are in `.env` and nowhere else.

Traces: the router sends none. gen9-agent traces each call to Langfuse once, under the model that
answered (gen9-agent/README.md, "Models").

## Budgets

Two limits bound what Gen9 can spend, both set in `.env` and applied on the next
`docker compose up -d --wait`:
- **Each person's.** A new install limits each person to $1 a day, so no one person's runs, or
  a stolen account's, can spend without end (OWASP API Security Top 10, API4), and so no one
  person can use up gen9-agent's day for everyone: $1 is a fifth of its $5. LiteLLM gives an end
  user one budget period, and a second, per model (`model_max_budget`), needs its enterprise
  licence (docs/plans/manual-e2e.md, P6-E2), so the period is the day the shared key counts in.
  A `1d` budget resets at 00:00 UTC; a `30d` one is a calendar month to LiteLLM, resetting on the
  1st at 00:00 UTC for everyone at once (`litellm_core_utils/duration_parser.py`,
  `_handle_day_reset`). An install from before keeps what its `.env` says ($20 a `30d` month on
  one from before this change): set `GEN9_USER_BUDGET_USD` and `GEN9_USER_BUDGET_PERIOD` there.
- **gen9-agent's, whoever it works for.** Its key may spend at most `GEN9_AGENT_BUDGET_USD` a day
  ($5), the backstop for what one person's limit doesn't cover: many people at once, or a loop
  or bug outside a person's turns. Past it, every call gets the router's budget error until the
  day resets; raise it for a busy install.

Each answer is bounded too: every chat alias stops one at 32,000 tokens (`max_tokens` in
`config.yaml`, which gen9-agent doesn't send, so it holds). Without it one call once wrote
114,559 tokens of a tool call's arguments for 13 minutes (docs/plans/manual-e2e.md, P6-Z1); the
model's own limit is 128,000. A model put on a chat alias must allow that much output. And
gen9-agent bounds each turn: an agent stops after 50 model calls, and a turn's agents after
150 together (gen9-agent/README.md, "Models"). For scale, on GPT-6 Luna the busiest hour of checks so far (740 calls, 5.4 million
tokens) cost $0.18.

| Variable | Meaning | Default |
| --- | --- | --- |
| `GEN9_USER_BUDGET_USD` | USD a user may spend per period; empty for no limit | `1` for a new install (earlier installs: what their `.env` says) |
| `GEN9_USER_BUDGET_PERIOD` | The period, such as `1d`, `7d`, `30d` | `1d` |
| `GEN9_USER_RPM` | Model requests a user may make per minute; empty for no limit | empty |
| `GEN9_AGENT_BUDGET_USD` | USD gen9-agent's key may spend per day, for everyone together; empty for no limit | `5` |

How it works:
- The `keys` job keeps a budget named `gen9-user-default` in line with those settings.
- `config.yaml` makes it every end user's default (`max_end_user_budget_id`).
- gen9-agent sends each call with the user's `sub` (`x-litellm-end-user-id`), and the router
  records the spend under it.

A user over budget gets "You've reached your model usage limit for now…" in the chat. That run
isn't retried, and other users go on. Over `GEN9_USER_RPM`, the router answers `throttling_error`
("Rate limit exceeded for end_user"): the run waits for Retry at once with "You've sent more
requests this minute than your limit allows. Retry in a minute." Every call made for the person
counts, the chat's title, memory and search indexing included, so a turn takes several. A budget of the user's own overrides the default. Set it
through the admin API with the master key: `POST /budget/new` (`budget_id`, `max_budget`,
`budget_duration`), then `POST /customer/update` (`user_id`, `budget_id`), or `/customer/new` for a
person the router hasn't seen. `/customer/update` takes a `max_budget` too, but no period, so that
limit would never reset. e2e/models.mjs does it for the seeded user.

A key can also name a default budget for its end users (`end_user_budget_id`). In v1.102.1 that
one was stored but never enforced (gen9-agent/explore/models/NOTES.md); since v1.103.0 an end user
first seen through the key gets it (LiteLLM #41636: their own budget first, then the key's, then
the proxy-wide one). Gen9 keeps the proxy-wide default: one limit for a person, whichever of
gen9-agent's keys names them first. v1.103.0 also checks budgets again on each fallback target (#41379),
for setups whose primary model is free; Gen9's `chat` isn't, and a person over their limit is
refused before any model runs.

## Verify

```bash
key=$(sed -n 's/^GEN9_MODELS_KEY=//p' ../gen9-agent/models.local.env)
curl -s -H "Authorization: Bearer $key" http://127.0.0.1:19000/v1/models | grep -o '"id":"[a-z-]*"'
curl -s -H "Authorization: Bearer $key" http://127.0.0.1:19000/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"model": "chat", "messages": [{"role": "user", "content": "Reply with one word: pong"}]}'
docker run --rm --network gen9-keycloak busybox:1.37 nc -z -w 3 gen9-models 4000   # fails: other networks can't reach it
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:19001/users/nobody/erase          # 401: the admin API needs gen9-agent's key
curl -s -X POST -H "Authorization: Bearer $key" http://127.0.0.1:19001/users/nobody/erase          # {"deleted": {…: 0}}
api=$(sed -n 's/^GEN9_MODELS_KEY=//p' ../gen9-agent/models-api.local.env)                         # the API's key:
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $api" -H 'Content-Type: application/json' \
  http://127.0.0.1:19000/v1/embeddings -d '{"model": "embed", "input": ["hi"]}'                   # 200
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $api" -H 'Content-Type: application/json' \
  http://127.0.0.1:19000/v1/search/web -d '{"query": "hi"}'                                     # 403, and 403 for chat
evals=$(sed -n 's/^GEN9_MODELS_KEY=//p' ../gen9-agent/models-evals.local.env)                     # the evals' key:
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $evals" -H 'Content-Type: application/json' \
  http://127.0.0.1:19000/v1/embeddings -d '{"model": "embed", "input": ["hi"]}'                   # 403: chat only
```

## Operate

| Task | Command |
| --- | --- |
| Status | `docker compose ps -a` (`keys` shows `Exited (0)`: it ran) |
| Spend per user | `docker compose exec postgres psql -U litellm -d litellm -c 'select end_user, round(sum(spend)::numeric, 4) from "LiteLLM_SpendLogs" group by 1 order by 2 desc'` |
| Stop (keeps data) | `docker compose down` |
| Reset (**deletes keys and spend**) | `docker compose down -v`, then `./init-env.sh --force --agent-env-file ../gen9-agent/models.local.env --agent-api-env-file ../gen9-agent/models-api.local.env --evals-env-file ../gen9-agent/models-evals.local.env` |

## Upgrade

Follow LiteLLM's stable releases (`vX.Y.Z` without `-rc` or `-dev`); read their notes and the
[advisories](https://github.com/BerriAI/litellm/security) first. Then:

```bash
docker buildx imagetools inspect ghcr.io/berriai/litellm:<version>     # the index digest
docker run --rm ghcr.io/sigstore/cosign/cosign:v3.1.3 verify \
  --key https://raw.githubusercontent.com/BerriAI/litellm/0112e53046018d726492c814b3644b7d376029d0/cosign.pub \
  ghcr.io/berriai/litellm@<digest>
```

Pin `<version>@<digest>` in `compose.yaml` for both `litellm` and `keys`, then run
`docker compose up -d --wait`. LiteLLM migrates its schema at start.
