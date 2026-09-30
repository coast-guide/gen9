# Model router probe: LiteLLM Proxy

The first unit of "Model router" (docs/plans/harness.md). Files: `compose.yaml` (LiteLLM and its own
Postgres, a throwaway project), `config.yaml` (aliases), `env.sh` (generated secrets plus only the
keys the probe needs), `probe.py` (run from `gen9-agent/`: `uv run python explore/models/probe.py`).

## Setup

**Image.** `ghcr.io/berriai/litellm:v1.102.1`, pinned by index digest
`sha256:87f34979…20d02`:
- It is published for `linux/amd64` only (`docker buildx imagetools inspect`), so on Apple
  silicon it runs under emulation.
- Signature checked with cosign v3.1.3 (`ghcr.io/sigstore/cosign/cosign:v3.1.3 verify --key
  https://raw.githubusercontent.com/BerriAI/litellm/0112e53…/cosign.pub <image>@<digest>`). The
  claims validated, the transparency log entry was verified offline, and the signature matched the
  key pinned at the commit the release notes name.

**Start.** 82 s from `docker compose up -d --wait` to healthy, including its Prisma migrations.
Idle it uses about 590 MiB, and its Postgres 84 MiB.

**Settings.** `DISABLE_ADMIN_UI=True`, `STORE_MODEL_IN_DB=False` (models only from `config.yaml`),
a master key, and one virtual key per caller made through `/key/generate` with only the aliases.

## What each step showed

| Step | Observed |
| --- | --- |
| Deep Agent (`create_deep_agent`) on `ChatOpenAI(model="chat", base_url=router)` with a tool, `stream_mode="messages"` | 29 streamed chunks, the `add` tool called, the second question answered `42` |
| Vision (`image_url` data URL) on alias `vision` | Answers "Red" for a 256 px red square, 4 of 4, the same as calling OpenAI directly. At 32 px `gpt-5.4-nano` is unreliable either way, so it's the model, not the router |
| Embeddings (`OpenAIEmbeddings(model="embed", check_embedding_ctx_length=False)`) | 2 vectors of 1536 dimensions |
| Speech (`audio.speech`, alias `speak`), then transcription of it (alias `transcribe`) | 37,632 bytes of audio, transcribed back as "LIM routes every kind of model." Transcription needs `response_format="json"`: without it LiteLLM asks upstream for `verbose_json`, which `gpt-transcribe` refuses with a 400 |
| Fallback: alias `chat-broken` (a bad key), `fallbacks: [{"chat-broken": ["chat"]}]` | Answered through `chat`; headers `x-litellm-attempted-fallbacks: 1`, `x-litellm-model-group: chat`, `x-litellm-model-name: openai/gpt-5.4-nano` |
| End-user budget (`/customer/new`, `max_budget` 1e-7), calls with `user="probe-over"` | 1st allowed (spend is checked before the call, then recorded), 2nd and 3rd refused: **HTTP 429**, `{"error": {"type": "budget_exceeded", "message": "ExceededBudget: End User=probe-over over budget. Spend=8.05e-06, Budget=1e-07", "code": "429"}}`. Another user was unaffected. |
| Attribution | `ChatOpenAI(model_kwargs={"user": sub})` records spend per end user (`/customer/info?end_user_id=…` → 0.00151 after the agent's calls) |
| Langfuse, `callbacks: ["langfuse_otel"]` | See below |
| Overhead | Median 1,006 ms routed against 814 ms direct over 8 calls each (`gpt-5.4-nano`, "Say ok."): about 190 ms, under amd64 emulation, including key checks, spend logging and the Langfuse export |

## Langfuse: the router's traces and the app's

**Without a `traceparent` header,** each router call is its own trace. It holds a
`litellm_request` generation and a `raw_gen_ai_request` span.

**With `traceparent: 00-<trace id>-<span id>-01`,** the router's generation nests inside the app's
trace, under the given span. Observed in trace `790e4606…`: the app's span `router-probe` held both
LangChain's `ChatOpenAI` generation and the router's `litellm_request`.

What each generation carries:

| Generation | `model` | Usage | Cost | Real model |
| --- | --- | --- | --- | --- |
| `ChatOpenAI` (LangChain `CallbackHandler`, app side) | `chat` (the alias) | yes | none: Langfuse can't price an alias | no |
| `litellm_request` (router, OTel) | `chat` | yes | yes (`attributes.llm.cost.total`) | yes, in metadata (`attributes.llm.response.model`, `…provider`, `…key_alias`) |

So tracing on both sides counts each call's tokens twice in Langfuse, while cost comes only from the
router. What gen9-agent does about it is decided in the unit that moves it onto the router (plan).

## Consequences for the build

- **Retries.** A budget refusal is a 429 with `type: budget_exceeded`. gen9-agent must classify it as
  permanent (no Temporal retry, a message in the chat), unlike an upstream rate limit.
- **Transcription** calls pass `response_format="json"`.
- **Vision checks** use images of a realistic size.
- **Emulation.** The stack states `platform: linux/amd64` until LiteLLM publishes arm64. That's slow
  to start on Apple silicon, but the runtime overhead is small next to model latency.

## Found while moving gen9-agent onto the router

- **Whose call.** The router always reads the end user from `x-litellm-end-user-id` (or
  `x-litellm-customer-id`), with no configuration (`STANDARD_CUSTOMER_ID_HEADERS`). gen9-agent sets
  it from a context variable through an httpx request hook, so every call of a run carries it.
  Spend then lands under each user's `sub`, seen for both seeded users.
- **Default budgets.** A key's default budget for its end users (metadata `end_user_budget_id`)
  was stored and never enforced in v1.102.1. The top-level `end_user_budget_id` of `/key/update`
  answered 200 and wasn't even stored. Over-budget users were allowed with and without a router
  restart, whether the user came from the body `user` field or the header.
  The proxy-wide `litellm_settings.max_end_user_budget_id` is enforced for existing users on the
  next call (429 `budget_exceeded`), and clearing its limits (`/budget/update` with nulls) lifts it
  at once. `/budget/info` answers 200 with `[]` for a budget that doesn't exist.
- **How the refusal travels.** langchain-openai wraps the 429 as `OpenAIRateLimitError`, a subclass
  of `openai.RateLimitError` with `type == "budget_exceeded"`. The run's row keeps the innermost
  cause (`workflows/runs.py`, `_describe`), which is the router's error body, so the public message
  is chosen from `budget_exceeded` in it. One attempt, then the chat shows the budget message.
- **Which model answered.** A streamed Responses API answer through the router names the dated
  model in `response_metadata["model_name"]` (`gpt-5.5-2026-04-23`). A chat-completions answer
  names the alias (`chat`); the real one is only in the `x-litellm-model-name` header.
  Langfuse's LangChain handler reads the model from `llm_output`, which streaming leaves empty, so
  gen9-agent's handler fills it from `response_metadata`. Langfuse then prices it from its table
  (it has `gpt-5.5`, `gpt-5-mini`, `text-embedding-3-small`). A refused call keeps the alias:
  there was no answer to name a model.
- **Langfuse v2 API.** `userId` is set on the run's root observation, not on its generations: find
  the user's traces first, then their generations.
- **Web search** (OpenAI's built-in `web_search` tool, Responses API) streams through the router
  unchanged: e2e/runs.mjs sees its "Searched the web" step.
- **Spend logs** hold usage and cost per call under the end user, not the messages
  (`messages`/`response` are `{}` by default).

## Erasing a user from the router

- `/customer/delete` refuses gen9-agent's virtual key: 401, "Only proxy admin can be used to …
  delete". It removes the end-user row only.
- LiteLLM has no API that deletes spend logs (`/spend/logs` is read-only). Its open-source
  retention (`maximum_spend_logs_retention_period`) deletes by age for everyone.
- Three tables keep something about one end user: `LiteLLM_SpendLogs.end_user`,
  `LiteLLM_DailyEndUserSpend.end_user_id` and `LiteLLM_EndUserTable.user_id`.
  `Last30dTopEndUsersSpend` is derived. The daily totals are written in batches, about a minute
  after the calls.
- The pinned image's Python 3.13 carries FastAPI 0.136.3, uvicorn 0.51.0, httpx 0.28.1 and
  psycopg 3.3.3, enough for Gen9's admin service with no image of its own.
- Live, for a throwaway user with a chat and an embedding call:
  - before, 2 spend logs, 2 daily rows and 1 end-user row;
  - the admin API's erase deleted exactly those, and repeating it deleted nothing;
  - afterwards a search of every text, JSON and array column in the database found the id 0
    times.
- The `gen9_admin` role got "permission denied" reading `LiteLLM_VerificationToken` and updating
  `LiteLLM_EndUserTable`.

## Web search through the router: experiment

Setup: a throwaway Compose project outside the repository (`exp-search`, port 19090).
- LiteLLM v1.102.1 (the same pinned image) with `search_tools` in its config.
- SearXNG (`searxng/searxng`, AGPL-3.0, pinned by digest; `search.formats: [html, json]`,
  limiter off) in the same project.

The router's search API: `POST /v1/search/{search_tool_name}` with Perplexity's Search API body
(`query`, `max_results`, `search_domain_filter`, `country`). Answers `{"object", "results": [{title,
url, snippet, …}]}`. Tools are configured like models: `search_tools: [{search_tool_name,
litellm_params: {search_provider, api_base | api_key}}]`.

| Tool | Query "PostgreSQL latest stable release", 3 results | Note |
| --- | --- | --- |
| `searxng` (self-hosted) | postgresql.org release notes, endoflife.date, postgresql.org downloads; snippets of 145–281 characters | Works with no key; the SearXNG container is its own AGPL service, not linked code |
| `duckduckgo` | none | It calls DuckDuckGo's Instant Answer API (`api.duckduckgo.com`, `litellm/llms/duckduckgo`). For "Python programming language" it returned Wikipedia and DuckDuckGo topic pages: encyclopedic answers, not web search |

Hosted providers (Tavily, Exa, Brave, Perplexity, …) need keys; none was tried.

For the plan: web search moves into gen9-models as a search tool alias. It is served by SearXNG
(self-hosted, keyless) or a keyed hosted provider, and a Gen9 tool calls the alias.

## Self-hosted models behind the router: experiment

Setup: a throwaway Compose project outside the repository (`exp-local`, port 19091). Ollama 0.34.4
(`ollama/ollama`, MIT, pinned by digest, native arm64) and the same pinned LiteLLM.
- `qwen3:0.6b` (522 MB) as `chat-local` (`ollama_chat/…`), and `embeddinggemma` (621 MB) as
  `embed-local` (`ollama/…`). Both pulled in 85 s. Ollama used about 1 GiB with both loaded.
- Through the router with `ChatOpenAI`: "Reply with one word" gave `ok` (9.4 s, first call includes
  loading). `bind_tools` returned the right call, `add{'a': 1234, 'b': 4321}` (8.2 s), on CPU.
- Embeddings need `drop_params: true`: the OpenAI SDK always sends `encoding_format` (`base64` by
  default), which the Ollama provider refuses with 400 ("UnsupportedParamsError"). gen9-models'
  config already sets it. With it: 768 dimensions, 8 texts in 2.5 s through `OpenAIEmbeddings`.

For the plan:
- A local profile can serve `embed` and small tool-calling chat.
- `chat` itself must stay on OpenAI until web search leaves OpenAI's built-in tool.
- Embedding dimensions differ by model (1536 for OpenAI's small, 768 here), which the search unit's
  vector columns must account for: re-embed on a model change.
- In gen9-models, `POST /v1/search/web` with `max_results: 3` returned 32 results:
  the router doesn't pass the limit to SearXNG. gen9-agent's tool cuts the list itself. SearXNG
  idles at 187 MiB.

## How narrow a virtual key can be

Why: search by meaning needs gen9-agent's API to embed each query through the router. The API
container held the worker's key, which can call every model and the admin API. `key_scope.py`
(throwaway keys against the running stack, LiteLLM v1.102.1, no enterprise license) showed:
- **`models: ["embed"]` is enforced on every model route.** Embeddings with `embed`: 200.
  `embed-local`, chat completions, the Responses API and images: 403 `key_model_access_denied`.
- **It doesn't cover search tools.** `POST /v1/search/web` still answered 200.
- **Search tools have their own allow-list,** `object_permission.search_tools`
  (`proxy/auth/auth_checks.py`, `_can_object_call_search_tool`). An empty list means every tool,
  so a key with none names one that doesn't exist: `["none"]` gave 403 "Key not allowed to access
  search tool: web".
- **Management routes refuse a virtual key:** `/key/generate` and `/spend/logs` gave 401 "Only
  proxy admin can be used…".
- **The router caches keys for about 60 s.** Updating a used key's existing `object_permission`
  row took 66 s to apply. Creating the key with its scope, or adding the row to a key that had
  none, applied at once.

For the build: the API gets a key of its own, `embed` only and no search tool, created with that
scope and read back by `ensure-keys.py`. The worker's key stays out of the API container.

## Sources of the model's web search, through the router

Why: research answers written in a later model response than their searches arrive with no
links (docs/plans/harness.md, Surprises, "Sources"), yet OpenAI requires that citations from web
results be "clearly visible and clickable" (developers.openai.com, "Web search" guide).

Probe (the scratch `sources_probe.py`): `ChatOpenAI(..., include=["web_search_call.action.sources"])`
with the built-in `web_search` tool, through gen9-models by alias.
- **LiteLLM v1.102.1 passes `include` through.** One `web_search_call` of type `search` came back
  with `action.sources`: 19 entries of `{type: "url", url}`, for example
  `https://valkey.io/download/releases/`. The guide calls these "the complete list of URLs the
  model consulted", and there are often more of them than citations.
- **Citations are separate:** the answer's text block had one `url_citation` annotation, `{url,
  title}` (title "Valkey"), with `?utm_source=openai` on the URL.
- **Sources have no titles**, only URLs. Citations have titles.

For the build: keep each search's sources on its step, and the answer's citations on its message,
so gen9-ui can show every answer's sources whatever the model wrote.

## OpenRouter through the router, and GPT-6 Luna: probes

Setup: a throwaway LiteLLM v1.102.1 (the pinned image) with an echo server standing in for
OpenRouter, to see the exact request; then direct calls to OpenRouter and calls through the
running router, a few cents in all. The choices these led to are in docs/plans/harness.md
(Decision Log).

What reaches OpenRouter (echo server):
- A `provider` object in an alias's `litellm_params` becomes a top-level field of the request,
  and so does one inside `extra_body`. A YAML anchor shared by the aliases works.
- A client's `session_id` and `prompt_cache_key` pass through. LiteLLM always adds `usage:
  {include: true}` and reads the cost from the answer.
- LiteLLM sends `HTTP-Referer: https://litellm.ai` and `X-Title: liteLLM`, which only credit the
  calls to LiteLLM in OpenRouter's rankings.

Routing, called directly:
- `data_collection: deny` kept GPT-6 Luna on OpenAI, which doesn't train on prompts. `zdr: true`
  moved it to Azure.
- `require_parameters: true` with LangChain's usual parameters got 404 "No endpoints found that
  can handle the requested parameters": no GPT-6 Luna endpoint lists `temperature` or
  `parallel_tool_calls`.
- DeepSeek-V4.1-Flash's tool call went to DeepInfra (fp8), by Auto Exacto's default ordering.

Costs, in the router's spend log:
- Chat Completions through OpenRouter are priced with OpenRouter's cost, streamed too.
- Embeddings through OpenRouter logged $0: LiteLLM has no price for Qwen3-Embedding-8B.
  `input_cost_per_token` in the alias fixes it (802 tokens: $8.02e-06).
- The Responses API through OpenRouter logged $0 for every call.
  `llms/openrouter/responses/transformation.py` (77 lines, the same on `main`) never reads
  `usage.cost`.

GPT-6 Luna:
- It reasons while calling tools on Chat Completions through OpenRouter: 37 reasoning tokens for
  a task that needed a calculation, 39 on the Responses API. A trivial task used none.
- Its tool calls, streaming and images work through the router. Asked "what colour is this
  image?" about a flat red square, it said "Black" 2 times in 3, on OpenAI and on Azure alike. A
  red square on white was "Red" 5 times in 5.

The model's name:
- On Chat Completions the router's body says the alias, in stream chunks too. The model that
  served the call is in `x-litellm-model-name` (`openrouter/openai/gpt-6-luna`).
- The Responses API keeps it in `response.model`.
- `include_response_headers=True` in langchain-openai puts every header in the answer's
  `response_metadata["headers"]`, streamed too. A callback's `on_llm_end` sees the same message
  object the caller gets, so it can remove them.

Not through OpenRouter with this LiteLLM version:
- OpenAI speech: no OpenAI TTS model is listed. The slug in OpenRouter's own TTS guide returns
  404.
- Transcription: OpenRouter's STT takes base64 JSON, not OpenAI's multipart form.
- `gpt-image-2`: LiteLLM sends it to `chat/completions`, which OpenRouter refuses ("Use the
  /api/v1/images endpoint instead").

## Web search under load: SearXNG and the router's fallbacks

Why: a whole `make e2e` came back with no sources, because every search in that window was
empty. The router's SearXNG logged 176 suspensions and timeouts in 40 minutes.

LiteLLM's search router: a throwaway LiteLLM v1.102.1 and a fake SearXNG.
- With `web`'s provider unreachable and `router_settings.fallbacks: [{"web": ["web-backup"]}]`,
  `web` answered from `web-backup` (`x-litellm-attempted-fallbacks: 1`).
- A provider answering 200 with no results doesn't fall back: 0 results, 0 fallbacks.
  LiteLLM's SearXNG adapter builds results from `results` without looking at
  `unresponsive_engines`.
- Upstream: #41391, GET search adapters turning HTTP errors into empty results, was fixed by
  PR #40779 (merged 2026-09-11), which v1.102.1 contains. #38628, SearXNG's own 429 or 503, is
  still open.

SearXNG, throwaway instances of the pinned image, the same 15 queries alternating:

| Settings | Empty searches | Engines that failed most |
| --- | --- | --- |
| Defaults | 10 of 15, then 15 of 15 | DuckDuckGo 15, Brave 14–15, Google CSE 10–15, Wikidata 6–15 |
| Defaults, 6 s timeout, no Wikidata | 13 of 15 | DuckDuckGo 15, Brave 15, Google CSE 13 |
| Defaults, no Wikidata, plus Google, Yahoo and Bing | 0 of 15, median 15 results | Bing 11, Brave 14, DuckDuckGo 15, Google CSE 15 |

Engine by engine, 5 queries each (`engines=<name>`):
- answered 5 of 5: Bing, Yahoo, Google's scraper;
- failed: Mojeek, Startpage and Brave ("Suspended: too many requests"), Qwant (CAPTCHA),
  DuckDuckGo (timeouts).

SearXNG's documented shape for this is `engines: - name: bing, disabled: false` under
`use_default_settings` (docs.searxng.org, settings). The fault is upstream blocking, not
timeouts: a longer timeout made no difference. Scraped engines block bursts; Bing was
throttled within the same 15 queries.
