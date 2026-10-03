<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="gen9-design/brand/wordmark-on-dark.svg">
    <img alt="Gen9" src="gen9-design/brand/wordmark.svg" height="56">
  </picture>
</p>

<p align="center"><strong>Autonomous agents you host yourself.</strong><br>
Configure what they know, the tools they use, and what they may do without asking.</p>

Gen9 is an agent platform you host yourself: a general-purpose AI agent your team gives tasks to, from the browser or the terminal. It plans the work, uses the tools and services you connect, runs code in a sandbox of its own, asks before it changes anything, and keeps going after the tab is closed. What it is good at is yours to set: skills, connectors, helpers and plugins.

## What it does

- **Takes on tasks, not only questions.** It plans, uses its tools, and runs commands in an environment of its own ([gen9-sandbox](gen9-sandbox/README.md)). Every run is a durable workflow, so it keeps going when nobody watches and survives a restart ([docs/temporal.md](docs/temporal.md)).
- **Asks before it acts.** Each chat has a permission mode; the agent's [questions](gen9-agent/README.md#questions) and [approvals](gen9-agent/README.md#approvals) wait for the person, as long as it takes.
- **Shaped to your work.** [Skills](gen9-agent/README.md#skills), [connectors](gen9-agent/README.md#connectors) (remote MCP servers, with their apps), [memory](gen9-agent/README.md#memory), [search over past chats](gen9-agent/README.md#search) and [plugins](gen9-agent/README.md#plugins) an admin chooses who may use.
- **Works on its own when you want it to.** [Scheduled tasks](gen9-agent/README.md#scheduled-tasks) and [background tasks](gen9-agent/README.md#background-tasks), fired on a schedule or over HTTP, with an email when one is done or needs someone.
- **Any model, one router.** Every model by alias through LiteLLM, with a budget per person ([gen9-models](gen9-models/README.md)).
- **Your team's accounts.** Sign-in with Keycloak, passkeys and two-factor (required of admins), admins and members ([docs/auth-architecture.md](docs/auth-architecture.md)).
- **Seen and measured.** Every run traced in Langfuse, [evals](gen9-agent/README.md#evals) of real tasks, and an audit record nobody can change.
- **Talks to other programs.** Gen9 is also an [MCP server](gen9-agent/README.md#mcp-server), an [AG-UI](gen9-agent/README.md#ag-ui) and an [A2A](gen9-agent/README.md#a2a) agent, and has a terminal client ([gen9-cli](gen9-cli/README.md)).

## Quick start

You need Docker with Compose 2.24 or newer (at least 8 GiB of memory for Docker), GNU Make, bash and openssl ([requirements](docs/operations.md#requirements)). Then:

```bash
make setup     # generates every secret and setting; asks for your model provider keys
make up        # starts every stack and waits until each is healthy
```

Open http://localhost:14000 and sign in as `ada@gen9.test` (admin) or `alan@gen9.test`, with the passwords from `grep ^GEN9_SEED_ gen9-keycloak/.env` (Ada's code: `make admin-code`), or create an account. What `make setup` asks for, and why: [docs/operations.md, "First-time setup"](docs/operations.md#first-time-setup).

## How it is built

A set of decoupled services, one folder each. Every Docker-based service is its own Docker Compose stack; the root `Makefile` starts and stops them together ([how they stay decoupled](docs/development.md#how-stacks-stay-decoupled)).

| Folder            | What                                                                      | Host ports (`127.0.0.1`) |
| ----------------- | ------------------------------------------------------------------------- | -------------------------- |
| [`gen9-langfuse`](gen9-langfuse/README.md) | Self-hosted Langfuse (tracing, evals)                                     | `13000–13007`           |
| [`gen9-ui`](gen9-ui/README.md)       | Next.js web app, auth BFF (`prod`; `dev` on demand), connectors' app sandbox + Valkey sessions | `14000–14003`           |
| [`gen9-keycloak`](gen9-keycloak/README.md) | Keycloak identity provider (own Postgres, Mailpit, Gen9 theme)            | `15000–15003`           |
| [`gen9-postgres`](gen9-postgres/README.md) | Postgres 18 + pgvector + pg_textsearch (BM25), the application database  | `16000`                  |
| [`gen9-agent`](gen9-agent/README.md)    | Agent API (FastAPI + Deep Agents, Keycloak-protected) and the workers that run it | `17000`                  |
| [`gen9-temporal`](gen9-temporal/README.md) | Temporal: durable execution for runs, approvals, schedules, deletions (own Postgres, web UI) | `18000–18001`           |
| [`gen9-models`](gen9-models/README.md)   | The model router: every model by alias, wherever hosted (LiteLLM Proxy, own Postgres, Gen9's admin API) | `19000–19001`           |
| [`gen9-sandbox`](gen9-sandbox/README.md)  | Environments: OpenSandbox runs each chat's commands and files in containers of its own | `20000`                  |
| [`gen9-edge`](gen9-edge/README.md)   | Optional: Gen9 under one domain, over TLS (Caddy), once `make setup DOMAIN=…` has set it up | `80`, `443` |

Also in the repository, none of them a stack:

| Folder | What |
| --- | --- |
| [`gen9-learn`](gen9-learn/README.md) | Everything about Gen9 in one page, for anyone starting from nothing: open `gen9-learn/index.html`. What Gen9 is and how it's built, then one user from sign-up to deletion watched in DevTools, every store and the logs, with deeper steps for the rest, and a Reference that names every service, workflow, route, table, setting and command, checked against the running system |
| [`gen9-design`](gen9-design/README.md) | The design system (tokens, font, logo) that the apps copy in |
| [`gen9-cli`](gen9-cli/README.md) | The terminal client: `gen9 login` signs in with a one-time code confirmed in a browser, then `gen9 ask` |
| [`e2e`](e2e/README.md) | End-to-end checks in real Chrome against the running stacks |
| [`docs`](docs/) | How Gen9 is operated, developed, secured and designed; the plans of work |

## Documentation

| To | Read |
| --- | --- |
| Install, run, upgrade, back up, stop the agents, start over | [docs/operations.md](docs/operations.md) |
| Understand Gen9 end to end, by watching it work | [gen9-learn](gen9-learn/README.md) (open `gen9-learn/index.html`) |
| Change Gen9: how the work is done, checked and proposed | [CONTRIBUTING.md](CONTRIBUTING.md), [docs/development.md](docs/development.md), [AGENTS.md](AGENTS.md) |
| Identity and tokens; secrets; durable execution; the EU AI Act | [docs/auth-architecture.md](docs/auth-architecture.md), [docs/secrets.md](docs/secrets.md), [docs/temporal.md](docs/temporal.md), [docs/ai-act.md](docs/ai-act.md) |
| The design system and every screen | [docs/design/](docs/design/), [gen9-design](gen9-design/README.md) |
| Anything else, by topic | [AGENTS.md, "Where things live"](AGENTS.md#where-things-live) |

**AI coding agents:** start at [AGENTS.md](AGENTS.md), the instructions file most coding agents read by themselves ([agents.md](https://agents.md/)); `CLAUDE.md` imports it. It holds the rules of work, the checks, and the map of where each kind of knowledge lives, so humans and agents follow the same ones.

## Help, contributing, security

- Questions and bugs: [open an issue](https://github.com/coast-guide/gen9/issues/new/choose).
- Changes: [CONTRIBUTING.md](CONTRIBUTING.md). Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
- A vulnerability: report it privately, as [SECURITY.md](SECURITY.md) says, not in an issue.

## Licence

Apache License 2.0 ([LICENSE](LICENSE)). [NOTICE](NOTICE) names the material from other projects that the repository carries: the typeface, the Keycloak theme's starter, the copied UI components, the plugin schemas.
