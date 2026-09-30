# gen9-learn

Everything about Gen9 in one page, for anyone starting from nothing. It opens with what Gen9 is and for whom, its goals and limits, what's around it and how it's built; then you create one user and follow it from sign-up to deletion, watching every step in Chrome's DevTools, Valkey, the Postgres databases, ClickHouse, the logs and the code that did it; it ends with a Reference that names every service, workflow, Schedule, API operation, route, table, volume, setting and command. It is not a stack; nothing here runs in Docker.

**Open it:** `open gen9-learn/index.html` from the repo (it loads Gen9's font and logo from `../gen9-design`). The stacks must be running (`make up`). The core path takes about four hours in eleven parts; steps marked *Deeper* trace the rest of what a part touches, and can be skipped. Progress is kept in your browser.

How it's built: orientation first and reference last (arc42), the traced path as a tutorial with explanation kept in fold-outs (Diátaxis), and every output checked against the running system (docs as tests). The plan and its sources: `docs/plans/gen9-learn.md`.

| Part | You do | You learn |
| --- | --- | --- |
| 0. Your kit | Set up DevTools, DBeaver (4 connections), Valkey Admin, logs | Where each kind of state lives |
| 1. Birth | Sign up, verify the email, choose a password | The sign-in transaction, the callback, sealed web sessions, just-in-time users |
| 2. Using it | Ask, reload (also mid-answer), let it ask before acting, answer its question, check its memory, find an earlier chat, idle 5 minutes | Token checks, runs and workers on Temporal, streaming, approvals and questions mid-task, memory and past chats, tracing, silent refresh |
| 3. Handing it work | Schedule a task, run it now, fire it with its API trigger | Temporal Schedules, a firing's chat, trigger tokens, the audit record |
| 4. Giving it more | Ask for a research brief, connect an MCP server, let it run a command and share a file, add a plugin | Skills, web search through the router and Sources, connectors and their approvals, environments (OpenSandbox), plugins |
| 5. Securing it | Authenticator app, recovery codes, lockout, forgot password, passkey | Keycloak account actions, the reset flow, where lockouts live |
| 6. Sessions ending | Two browsers, every way to sign out | Back-channel logout |
| 7. A second client | `gen9 login`, `ask`, `logout` | The device flow and a public client |
| 8. Other agents call it | An MCP client signs in and asks (as a task), an AG-UI run, an A2A task | Gen9 as an MCP server, MCP Tasks, AG-UI, A2A, tokens per endpoint |
| 9. Death | Delete a chat, then the account, and try to erase its audit record | Step-up (`auth_time`), deletion order across stores, on Temporal; what outlives an account |
| 10. Running it | Restart stacks, list each container's secrets and database user, read the spend limits and the judge's calibration, lose a settings file, back up a dependency, wipe it and restore it | Realm import vs `configure.sh`, migrations, the per-stack networks, which secrets each service holds, budgets and a turn's step limit, evals, backups and their keys |

## What's here

| Path | What |
| --- | --- |
| `index.html` | The guide: one self-contained page, no network needed |
| `tools/peek-session.sh EMAIL` | Read-only: decrypts a user's web sessions inside gen9-ui's container and prints the record's fields and token claims, never a token |
| `verify/` | Re-runs the whole story live and every runnable command in the page (see below) |

## Verifying the guide

Everything the page shows as "verified" was observed by `verify/`, and it can be re-run at any time:

```bash
cd gen9-learn/verify
npm ci
node run.mjs            # the whole story, deeper steps included (two real waits of 3 to 5 minutes)
QUICK=1 node run.mjs    # skips the waits (refresh and step-up aren't exercised)
node page.mjs           # the page only, no stacks: widths, light/dark, axe, no sideways scrolling
node reference.mjs      # the Reference is complete, and every pointer into the code lands on what it names
```

`run.mjs` drives real Chrome (as `make e2e` does) with a throwaway user `trace-<time>@gen9.test`, records what DevTools shows (hops, statuses, cookie attributes, console), checks Valkey, the databases, ClickHouse and the logs at each step, and runs every command marked `data-check="run"` or `"run-any"` in `index.html` from the repo root. The user is deleted at the end whatever happens. Recordings go to `verify/out/` (ignored by git): codes, states, tokens and cookie values are replaced by `…`, and command output is never printed. When a batch stops on an error, the run prints the page's address and saves a screenshot, `out/failure.png`.

| Variable | Effect |
| --- | --- |
| `QUICK=1` | Skip the waits for the access token to expire and for step-up |
| `DESTRUCTIVE=1` | Also back up gen9-postgres, wipe it and restore it in part 10 (every chat goes, then comes back from the backup) |
| `KEEP=1` | Keep the throwaway user at the end, and save it to `verify/out/user.json` (git-ignored, readable by you only) |
| `REUSE=1` | Sign in again as the user a `KEEP=1` run saved, to re-run later batches without b1 and b2 (up to part 4: from part 5 on the user has an authenticator app) |
| `HEADED=1` | Show the browser |

Run a subset with `node run.mjs b1 b2 commands` (later parts need the earlier ones' user). The batches, in the order a full run takes them: `b1 b1d b2 b2d b2w b2wd b2x b2xd commands b3 b4 b4d b5 b5x b5xd b6d b6 b7 b7d`. Each `…d` batch verifies a part's deeper steps, right after the part it deepens; `b6d`, the data export, comes before `b6`, which deletes the account. Part 4 (`b2x`, `b2xd`) serves a plugin marketplace on port 17805 with e2e's git server (`e2e/fixtures/git-server.mjs`, plain Node) and signs the seeded admin in to add it; `b2xd` also starts e2e's MCP test servers on ports 17801 to 17804 (`uv`, fastmcp 4.0.9) and sends a throwaway environment secret to httpbin.org, which echoes it back. `b2d` stops gen9-models' router for a moment and swaps gen9-agent's worker for one with a small context budget; `b7d` runs `make stop-agents` and `make resume-agents`: both put everything back. Part 8 (`b5x`) signs an MCP client and an A2A agent in as the throwaway user; `b5xd` also signs in a client that registers itself (a Client ID Metadata Document served on host.docker.internal). A full run's model calls cost about a cent and a half (measured: $0.015 on the worker's key over full run 5).

## Why the page is built this way

| Choice | Evidence |
| --- | --- |
| A tutorial on rails, explanation kept in fold-outs | [Diátaxis](https://diataxis.fr/start-here/): tutorials are for learning by doing, explanation for understanding |
| The cast of terms before the first flow | Mayer's pre-training principle (d = 0.46) |
| One action and a few observations per step; the reader sets the pace | Mayer's segmenting (d = 0.70) and coherence (d = 0.70) principles |
| "Predict" before each observation | [PRIMM](https://computingeducationresearch.org/projects/primm/): predict, run, investigate |
| "Check yourself" after each part, "Review later" days after | Retrieval practice, g = 0.61 over 217 studies, strongest with mixed question types ([Adesope et al. 2017](https://journals.sagepub.com/doi/abs/10.3102/0034654316689306)) |
| Terser steps as the parts go on | The [expertise reversal effect](https://en.wikipedia.org/wiki/Expertise_reversal_effect): guidance that helps novices burdens experienced readers |
| The map that lights up per step | Signaling (d = 0.46): show where to look, next to what to do |

## Findings from building it

Verifying every step live turned up things worth knowing in production (all in the page):

- Deleting a chat kept its Langfuse trace (question and answer) until the account was deleted. Fixed: deleting a chat now erases its trace first.
- Keycloak doesn't save `REFRESH_TOKEN` events by default; a refresh shows as `offline_user_session.last_session_refresh`.
- Lockouts live in Keycloak's cache, not in the `login_failure` table.
- Keycloak 26 keeps login sessions in its database, so they survive a restart.
- A search by meaning waits for the router to embed the query: seconds, and up to 23 s for one word when the provider was slow.
- A one-word query by meaning lands nearest the shortest chats, whatever the word means; a whole question finds what it's about, in another language too.
- Temporal's public frontend answers a caller without a token "Request unauthorized."; the operator's `cli` uses the internal frontend, with the internode certificate.
- Writing the Reference against the running system found a docstring naming a workflow that doesn't exist (`SyncRegistryWorkflow`, fixed), and settings, routes and Activities the page had never named.
