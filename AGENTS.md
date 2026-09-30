# Gen9: rules for agents and contributors

A map, not an encyclopedia: it says how we work and where each kind of knowledge lives. Read the
linked file before changing that area. These are rules for the people and AI agents who develop
this repository; they are not behavior of Gen9's own agent.

## Start of every session

1. `git status`, `git log --oneline -15` and `gh pr view` (if a PR is open): what changed last.
2. Read the active plan in `docs/plans/` (today `docs/plans/release.md`, then
   `docs/plans/gen9-learn.md`; `manual-e2e.md` is paused at P6-B6 by the owner, and its standing
   instructions still apply; `harness.md` is complete): its `Progress` says what is done and what is next, `Surprises & Discoveries` what
   not to try again, `Decision Log` why.
3. `make ps`: which stacks run. `make up` if they don't; `make doctor` if something is off.
4. Pick the first unchecked item in the plan's `Progress`, and work on that one thing.

## Before any new piece of work (mandatory)

Research, reason and plan before writing code, every time:

1. **Date.** Establish today's date; everything below is "as of" that date.
2. **Research.** Read today's primary sources: vendor docs, specs, release notes, changelogs, the
   libraries' source. Filter out AI-written summaries and listicles; a claim counts only when a
   primary source or a probe backs it. Look at how the leading products and projects already do it.
3. **Reason.** From that evidence, compare the options (adopt, adapt, build) and choose. Prefer
   battle-tested open source and established patterns; build only what nothing proven covers.
4. **Plan.** Write it into the active plan (`docs/plans/`): the Progress items, a Decision Log entry
   with the sources, and the acceptance checks that will prove it (`*-acceptance.json`).

Only then implement. The same applies to design and UX work, and to any change of direction mid-way.

## How we work

- **Web first when stuck.** Before debugging deep, guessing a config or running experiments, search
  that day's primary sources for the exact problem: the docs of the version in use, its
  changelog, the project's issues and its source. Filter out AI-written summaries. Only when that
  gives nothing credible, work it out yourself, by probe and reasoning.
- **Probe before building.** Settle an unknown with a small script that runs the real library
  (`gen9-agent/explore/`), and write down what it showed.
- **One logical unit at a time, verified live.** Build it, run it on the real stacks (real `make`
  commands, real Chrome through `e2e/`, real databases), then commit. Never batch verification at
  the end. A check that passes without exercising the path proves nothing.
- **Decoupled stacks.** Each `gen9-*` folder is its own Compose project, with its own `.env`,
  volumes and network; stacks reach each other only over `gen9-<stack>` networks and only the ones
  they call (README, "How stacks stay decoupled"). Don't merge stacks or share databases.
- **Keep the top level in sync.** A change to how something is run, started or checked updates the
  `Makefile`, the root `README.md`, the stack's README, `e2e/README.md` and `gen9-learn` (its page
  and verifier) in the same commit.
- **Python is async from the ground up.** This covers every Python service, worker, CLI, script and
  test.

  - `async def` for anything that does I/O, with async clients and drivers: SQLAlchemy asyncio with
    psycopg, `httpx.AsyncClient`, the SDKs' async APIs.
  - `asyncio.run` only at the entry point.
  - Nothing blocks the event loop: no `time.sleep`, `requests`, sync HTTP or database calls, or
    blocking file and `pathlib` I/O inside async code.
  - A library with no async API goes through `asyncio.to_thread`, with a comment saying why.
  - Tests are async too (pytest-asyncio).
  - Ruff's `ASYNC` rules (flake8-async) enforce what a linter can see, in each project's
    `ruff check` and in CI. Review the rest.
- **Use the waiting time.** Long checks run in the background (the gen9-learn verifier, `make e2e`,
  CI, image pulls). While one runs, do work that can't disturb it:

  - research and plan the next Progress items from primary sources;
  - run independent experiments the /rigor way, outside the repository: a scratch directory, and
    throwaway Compose projects or containers with their own names, ports and volumes, never the
    running stacks. Write what they showed into a durable place the same day: the probe notes
    (`gen9-agent/explore/*/NOTES.md`) and the plan's Surprises or Decision Log;
  - draft plan entries and docs;
  - review the diff;
  - write code and unit tests that don't touch the running stacks.

  What must wait until the check ends:

  - restarting, redeploying or reconfiguring anything it exercises (containers, `.env` files, a
    router's budget);
  - editing files it reads while it runs (gen9-learn's page during the `commands` batch);
  - a second browser check that signs in as the same seeded users;
  - heavy load on the same machine while a browser check runs: large image pulls, model downloads,
    emulated containers starting. It slows the stacks under test into timeouts (it failed
    gen9-learn's b3). Run such experiments between checks, or on a quiet stretch
    such as unit tests.

  Keep using that time while background jobs run. Check on a job when it notifies you; don't sit
  idle polling it.
- **Plans are living documents.** Update the plan's `Progress` at every stopping point, add
  surprises with evidence, add decisions with their reasons, and check an item only after it was
  verified live. The acceptance list (`docs/plans/*-acceptance.json`) changes `passes` only after
  its check ran.

## Rules

- Changes reach `main` only through a pull request, squash-merged: a ruleset blocks direct and
  force pushes. Commit messages and pull request descriptions: subject plus body only; no
  co-author, "assisted by", session or "generated with" lines.
- Model spend (the owner's standing instruction): every model call costs the owner
  money. Keep Gen9's aliases on the cheapest current models that work (gen9-models/README.md),
  ask for short answers in checks, run costly scenarios once, measure the router's spend log
  before and after, and run any subagents on a cheaper model. Work autonomously: the owner
  isn't there to ask.
- The verification work is a continuous loop of phases (the owner): when a phase's
  list is done, its last task is the next phase: /rigor first (date, primary sources, zero
  assumptions), then the next large list in `docs/plans/manual-e2e.md`, then work through it.
  Never stop. Keep a 5-minute keep-alive cron job running (create it if the session has none).
- Secrets: never print, commit or paste values of `.env` or `*.local.env` files; print key names.
  Scripts that need a user's token use `e2e/token.mjs` (device flow, confirmed in headless Chrome).
- Browser automation driven by an agent must not type passwords; password flows run in Puppeteer
  scripts (`e2e/`, `gen9-learn/verify/`).
- Destructive commands (`make wipe`, `make distclean`) only when the task needs them.

## Where things live

| What                                                               | Where                                                      |
| ------------------------------------------------------------------ | ---------------------------------------------------------- |
| Stacks, `make` commands, how stacks stay decoupled               | `README.md`, `Makefile`, each `gen9-*/README.md`     |
| Reporting a vulnerability, and where each part of Gen9's security is described | `SECURITY.md` |
| Active plans (goal, progress, decisions, surprises)                | `docs/plans/`, written as `docs/PLANS.md` says         |
| Operating Gen9: back up and restore, stop every agent, upgrade    | `README.md` ("Back up and restore", "Stop every agent at once", "Upgrade") |
| Architecture: identity and tokens                                  | `docs/auth-architecture.md`                              |
| Secrets: each one, where it lives, how to replace it              | `docs/secrets.md`                                        |
| UI/UX: principles, design system, screens                          | `docs/design/` and `gen9-design/` (tokens, font, logo) |
| Temporal: where Gen9 uses it, the rules, what is held back and why | `docs/temporal.md`                                       |
| The EU AI Act: what Gen9 discloses and marks (Art. 50)             | `docs/ai-act.md`                                         |
| Models: aliases, providers, the router's hardening                 | `gen9-models/README.md` (`config.yaml`)                |
| Agent API, runs, workers, events                                   | `gen9-agent/README.md`                                   |
| Environments: where a chat runs commands and keeps files (OpenSandbox) | `gen9-sandbox/README.md`, `gen9-agent/README.md`         |
| What probes showed (MCP, sandbox, durability, async subagents)     | `gen9-agent/explore/harness/NOTES.md`                    |
| End-to-end checks                                                  | `e2e/README.md` (`make e2e`)                           |
| Everything about Gen9 in one page: a guided trace and a Reference  | `gen9-learn/` (its `AGENTS.md` has its own rules)      |
| Web app conventions (this Next.js has breaking changes)            | `gen9-ui/AGENTS.md`                                      |

## Checks

- Everywhere: `make e2e` (every stack up), `make audit`, `make design-check`.
- gen9-agent and gen9-cli: `uv run pytest && uv run ruff check && uv run ruff format --check && uv run ty check src`.
  From gen9-agent, the other stacks' Python too, each by its own `ruff.toml`: `uv run ruff check
  ../gen9-models ../gen9-sandbox && uv run ruff format --check ../gen9-models ../gen9-sandbox`.
- gen9-ui: `npx tsc --noEmit && npx eslint . && npx vitest run && npm run build` (only the build
  enforces `server-only`). gen9-keycloak's theme: `npx tsc --noEmit`.
- The shell scripts: shellcheck, as the workflow's `shellcheck` step runs it (in Docker).
- gen9-learn: `cd gen9-learn/verify && node page.mjs && node reference.mjs`, and `node run.mjs` when a flow changed.
  `reference.mjs` also after any change to gen9-agent, gen9-ui or gen9-cli code: the page points
  into it by line (`data-at`), and an edit above a pointer moves it.

CI (`.github/workflows/checks.yml`) runs the non-interactive ones on every pull request and on `main`. Run them
locally before each commit anyway: CI is the second net. The live checks (`make e2e`, gen9-learn's `run.mjs`) run only
locally, on the stacks.
