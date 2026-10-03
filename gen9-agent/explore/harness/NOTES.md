# Harness probes: what each one showed

Phase 0 of the harness plan: small scripts that settle a design question by running it. Each entry
records the versions, the command, and what was observed.

## MCP client

Server: `mcp_server.py` (FastMCP 4.0.9, serves both protocol eras on one endpoint).

| Client | Versions | Era negotiated | Tools | Elicitation mid-call |
| --- | --- | --- | --- | --- |
| `mcp_adapters_probe.py`: standalone `langchain-mcp-adapters` | 0.3.2, mcp 1.30.0 (it pins `mcp<2`) | 2025-11-25 only | listed and called | fails ("Elicitation not supported") unless the caller writes a handler |
| `mcp_langchain_probe.py`: `langchain.mcp` in `langchain[mcp]` | langchain 1.4.2, fastmcp 4.0.9, mcp 2.2.0 | 2026-07-28 by default (`mode="auto"`), 2025-11-25 with `mode="legacy"` | listed and called | pauses the Deep Agent as a LangGraph interrupt (`type: mcp_elicitation`, `requests[].key`), resumes with `Command(resume={"responses": {key: answer}})`, the tool completes |

On a 2026-07-28 connection a server can't push `elicitation/create` (no back-channel, SEP-2322/2575):
a tool returns `InputRequiredResult` and reads `ctx.input_responses` when the client retries. FastMCP
4.0.9 raises a clear error if a tool calls `ctx.elicit` there.

Decision: Gen9 uses `langchain.mcp` (the namespace LangChain documents as replacing
`langchain-mcp-adapters`), behind Gen9's own connector module because the namespace is still marked
beta inside the stable langchain 1.4.2 release.

## Sandbox: OpenSandbox

Server: `opensandbox/server:release-1.1.0` in Docker with the Docker runtime (it starts each sandbox as
a sibling container through the mounted Docker socket), `execd:v1.1.0`, `egress:v1.1.7` in
`dns+nft` mode. The PyPI wheel `opensandbox-server==1.1.0` fails at import
(`No module named 'opensandbox_server.services.fast_sandbox.generated'`), so the image is the way to
run it. Probe: `sandbox_probe.py` (SDK `opensandbox==1.1.0`).

| Check | Observed |
| --- | --- |
| Create `python:3.12-slim` | 4.6 s; commands run as root (uid 0) in that image |
| SDK reaching the sandbox | Direct endpoints are `host.docker.internal:<port>`, unreachable from the macOS host; `use_server_proxy=True` goes through the server, so the agent needs to reach only the server |
| Network policy `defaultAction=deny`, allow `httpbin.org` | allowed host 200; any other host fails at DNS; `169.254.169.254` times out |
| Credential Vault (`credential_proxy`, apiKey binding on `httpbin.org/headers`) | upstream received the header; the secret appears nowhere in the sandbox's environment; TLS interception needed no setup in the image |
| Deep Agent with `langchain-sandbox-opensandbox` 0.1.2 as backend | `write_file` then `execute`; answered 832040 (30th Fibonacci) and the file is in the sandbox |

Decision: OpenSandbox is the default environment provider. Gen9 writes its own adapter: the
community one is sync-only, refuses to overwrite files in `upload_files` (Deep Agents' edits and
file sync need overwrites) and drops trailing newlines.

## Durable runs: crash and resume

Probe: `durable_probe.py` (deepagents 0.7.18, AsyncPostgresSaver on gen9-postgres, schema `langgraph`).
A Deep Agent calls an 8-second tool three times in order; the process is killed with `kill -9`
while step 2 runs, and a new process calls `ainvoke(None, config)` on the same thread.

```
+0 s   pid=6279 step 1 start        +24 s  pid=6400 resume: next=['tools']
+8 s   pid=6279 step 1 finish       +24 s  pid=6400 step 2 start
+10 s  pid=6279 step 2 start        +32 s  pid=6400 step 2 finish
        (killed)                    +34 s  pid=6400 step 3 start … finish, reply "done"
```

History afterwards: tool results `step 1 ok`, `step 2 ok`, `step 3 ok` once each, 4 AI messages (no
model call repeated), 10 checkpoints. So finished steps run once, and a tool interrupted mid-call
runs again (at least once): tools with side effects need an idempotency key (for example the tool
call id). A worker that dies mid-run loses nothing a new worker can't redo from the checkpoint.

## Async subagents over our own Agent Protocol endpoints

Probe: `agent_protocol_probe.py`. `AsyncSubAgentMiddleware` (deepagents 0.7.18) talks to a remote
agent through langgraph-sdk with five calls (source: `deepagents/middleware/async_subagents.py`):
`POST /threads`, `POST /threads/{id}/runs` (`multitask_strategy="interrupt"` for a follow-up),
`GET /threads/{id}/runs/{run}` (`status`: pending, running, success, error, interrupted),
`GET /threads/{id}` (`values.messages[-1].content` is the result) and
`POST /threads/{id}/runs/{run}/cancel`. A 90-line FastAPI server with those endpoints was enough:

```
> In the background, ask the researcher for the three smallest primes above 100.
  Started researcher task 6ea1a51a-….
> While that runs: what is 12 * 12?
  144
> Now check the background task and give me its result.
  101, 103, 107
```

So Gen9 agents can delegate to each other in the background through Gen9's own server, without
LangChain's Agent Server.

## Temporal: a run as a heartbeating Activity

Probe: `temporal_probe.py` (temporalio 1.33.0, dev server `temporalio/temporal:1.9.1 server
start-dev`, gen9-postgres checkpointer). A Deep Agent turn runs as one Activity that heartbeats
every 2 s (`heartbeat_timeout=10s`, `RetryPolicy(maximum_attempts=3)`,
`WAIT_CANCELLATION_COMPLETED`), and passes the same `metadata.run_id` on every attempt.

Worker killed with `kill -9` during step 2; a second worker was already running:

```
+0 s   pid=41970 step 2 start          (killed)
+9 s   pid=42112 attempt 2 started      9 s later: the heartbeat timed out, Temporal retried
+9 s   pid=42112 step 2 start           step 1 not repeated: resumed from the Postgres checkpoint
+25 s  pid=42112 attempt 2 finished: 'done'
```

Cancelling the Workflow during step 2: the Activity got `CancelledError`, and
`activity.cancellation_details().cancel_requested` told it apart from a worker shutdown; the
Workflow ended `CANCELED`. Delivery waits for the next heartbeat the worker sends, and the SDK
throttles heartbeats to 0.8 × the heartbeat timeout (`temporalio/worker/_worker.py`), so a
10 s timeout means up to ~8 s: Gen9 uses a ~3 s timeout for runs (cancel and dead-worker detection
within ~3 s).

A Workflow waiting on an Update (an approval) kept waiting while its worker was killed and a new
one started, then completed on `execute_update(decide, "approved")`.

The workflow sandbox re-imports the module that defines a workflow to validate it, so that module
must have no side effects at import (the probe first failed with "asyncio.run() cannot be called
from a running event loop"). Gen9 keeps workflow definitions in their own module.

Decision: runs move to Temporal as planned (docs/plans/harness.md).

## A runaway tool call stalls the worker

On k3d (`make k8s-e2e`, agents.mjs) the agent's model, gpt-6-luna through the router, ran away
inside a `task` call: 32,000 output tokens, its limit, over four minutes. The arguments began well
(`{"subagent_type":"fact-checker","description":"…","`), then held 190,000 characters of the model's
own deliberation in a JSON key it never closed. When the stream ended, the worker's event loop
stopped for over two minutes: its readiness probe failed from 20:59:21 to 21:00:51 (the alive
file older than 30 s), so `keep_token_fresh` (every 15 s, a new token once under 30 s were left)
never ran; Temporal refused the expired token from 20:59:42 ("Token is expired"), the core worker's
polls got PermissionDenied, and the worker stopped ("Worker failed, shutting down", 21:01). Idle,
the same worker ran 15 minutes without a refusal.

The cause is langchain-core. Once a stream ends it parses each tool call's arguments
(`AIMessageChunk.init_tool_calls`, `parse_partial_json`) on the event loop, and repairs text that
doesn't parse by closing what is open, then retrying `json.loads` once per character it drops from
the end, each retry reading the whole text: quadratic. `runaway_tool_call_probe.py` streams a Deep
Agent as the worker does (v2 parts, messages and updates, subgraphs) on a fake model with that
shape: one parse of the whole arguments, holding the loop throughout, 3.7 s for 40,000 characters
and 85 s for 193,000 (the loop stalled 85.4 s). langchain-ai/langchain#40826 (open) is the same function's cost on
another path; langchain-core 1.6.6, the latest, has it unchanged.

Gen9's `partial_json.py` is that function cutting straight back to where `json.loads` failed, since
no longer candidate parses past it: 193,000 characters in 0.03 s, the loop's longest stall 0.1 s.
On every prefix of a set of documents and on 4,000 random corruptions, strict or not, it returns
or raises what langchain-core's does (tests/test_partial_json.py), and on 60,000 more in a seeded
run (compared by `repr`: a NaN is never equal to itself, which flagged 22 equal results). And `keep_token_fresh` now keeps half the token's 5 minutes, so the event loop may
stall up to 2.5 minutes, whatever stalls it, with Temporal still shown a valid token.

## Model router

LiteLLM Proxy v1.102.1 behind LangChain and the OpenAI SDK: a Deep Agent with tools, vision,
embeddings, speech and transcription by alias; fallbacks; end-user budgets; Langfuse traces. Results
in [`../models/NOTES.md`](../models/NOTES.md).

## Search in Postgres

BM25 (pg_textsearch), fuzzy (pg_trgm), vector (pgvector HNSW, iterative scans) and hybrid (RRF)
over past-chat text, per user, on the stack's Postgres 18 image. Results in
[`../search/NOTES.md`](../search/NOTES.md).
