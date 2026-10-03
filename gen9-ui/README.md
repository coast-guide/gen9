# gen9-ui

The Gen9 web app: Next.js 16 (App Router, TypeScript, Tailwind CSS 4, shadcn/ui on Base UI) with the Gen9 design system. Signed-in users chat with the research agent, manage their account and, if they are admins, manage users.

Authentication is a **Backend-for-Frontend** ([RFC 10017](https://www.rfc-editor.org/info/rfc10017/)): this server signs users in with Keycloak and keeps the tokens in its own session store. The browser holds only an opaque session cookie.

**Requires:** Docker with Compose 2.24+ (`make doctor` checks it; Compose Watch alone needs 2.22), gen9-keycloak running (and gen9-agent for chat). Node.js 24 only if you run outside Docker.

## Quick start

```bash
./init-env.sh                               # .env: session secret + Valkey password
# keycloak.local.env comes from ../gen9-keycloak/init-env.sh --ui-env-file ../gen9-ui/keycloak.local.env
for n in gen9-ui gen9-keycloak gen9-agent; do docker network inspect $n >/dev/null 2>&1 || docker network create $n; done   # once; make up does it
docker compose up -d --build --wait         # production → http://localhost:14000
docker compose watch dev                    # development (Fast Refresh) → http://localhost:14001
```

`dev` is in the `dev` profile, so a plain `docker compose up` starts production (and Valkey) only.

## Screens

| Route | Who | What |
| --- | --- | --- |
| `/` | signed out | Home: what Gen9 is in one screen (the headline, one example task), *Sign in* / *Create an account* (thumb zone on phones) |
| `/chat`, `/chat/[id]` | signed in | Research chat: streamed answers with sources, the plan and steps, approvals and questions mid-task, Retry, files, a chat's environment, connectors' apps, background tasks, the permission mode; thread list, delete (docs/design/screens/chat.md) |
| `/search` | signed in | Past chats by words, meaning or title |
| `/scheduled` | signed in | Scheduled tasks: new, edit, run now, pause, resume, delete, an API trigger's token |
| `/settings` | signed in | Profile (with model use against the limit, and when it resets), memory and its switches, connectors (and tools a server changed, waiting for a look), plugins, skills, notifications, environment secrets (sent for reading only, or for changes too), password, authenticator apps and passkeys (add, list, remove), recovery codes (codes left, create new), where you're signed in (each browser, sign out one or all others), sign out everywhere, theme, download a copy of your data (a ZIP, `app/api/export`), delete account (type your email; asks you to sign in again if your last sign-in is over 5 minutes old), and a link to the privacy page |
| `/admin/users` | `gen9-admin` | Search users; grant/revoke admin; enable/disable (disabling stops what Gen9 is doing for them); unlock sign-in; email a password reset; sign out everywhere; delete a user and all their data (type their email; needs your sign-in from the last 5 minutes) |
| `/admin/plugins` | `gen9-admin` | Plugin sources (add, sync now, remove); every plugin with what it brings, its skills to read, who may have it, and what changed since it was made available |
| `/admin/audit` | `gen9-admin` | The audit log: who did what, newest first; everything, or refused access |
| `/privacy` | anyone | What Gen9 keeps, who else receives it, for how long, and a person's rights (see `PRIVACY_*` below) |
| `/signed-out`, `/auth/error` | anyone | After sign-out; sign-in problems in plain language |

Keycloak renders sign-in, sign-up, reset password, 2FA and email verification with the Gen9 theme (`gen9-keycloak/theme`). No password ever reaches this app.

## How auth works

| Piece | File | Notes |
| --- | --- | --- |
| Sign in | `app/auth/login/route.ts` | Authorization Code + PKCE (S256) + state + nonce via [openid-client](https://github.com/panva/openid-client) (OpenID Certified). `?intent=signup` → `prompt=create`; `?action=UPDATE_PASSWORD` etc. → Keycloak application-initiated actions |
| Callback | `app/auth/callback/route.ts` | One-time transaction (Valkey, 10 min) bound to this browser by a cookie; validates the ID token; new session id on every sign-in (no fixation) |
| Sessions | `lib/auth/session.ts`, `lib/auth/store.ts` | Valkey; records sealed with AES-256-GCM, each bound to its key in the store; TTL = refresh token lifetime; access token refreshed 30 s before expiry under a per-session lock (Keycloak rotates refresh tokens). Valkey is capped (`VALKEY_MAXMEMORY`, default 256 MB) and evicts the keys closest to expiry first, so a flood of sign-in starts can't exhaust memory or push out sessions. Its append-only file keeps sessions through a restart or a crash. While it's down, pages say Gen9 may be restarting (after about 5 s), sign-in says it's unavailable, and sign-out still ends the session in this browser and at Keycloak, which asks to confirm |
| Cookie | `lib/auth/cookies.ts` | Random 256-bit id, `HttpOnly`, `SameSite=Lax`; over https also `Secure` + `__Host-` prefix, and ended with the same attributes (a browser ignores a `__Host-` cookie set without them) |
| Sign out | `app/auth/logout/route.ts` | POST, same origin only; deletes the session, then RP-initiated logout at Keycloak (`id_token_hint`) |
| Back-channel logout | `app/auth/backchannel-logout/route.ts` | Keycloak POSTs a signed logout token when a session ends anywhere; at most 64 KiB of the body is read, before anything else; validated (signature, iss, aud, the type `logout+jwt`, events claim, no nonce, jti replay; `lib/auth/logout-token.ts`) → sessions with that `sid` are deleted |
| Route guard | `proxy.ts` | Optimistic only (no cookie → sign in). The real check is in the data access layer (`getSession`, `requireSession`), per Next.js 16 guidance |
| Content-Security-Policy | `proxy.ts`, `app/api/csp-report` | A per-request nonce of 128 random bits; what it blocks is reported and logged, one line per violation by origin and path (`[csp] img-src blocked https://… on /chat`), capped per minute, and at most 64 KB of a report is read: `report-uri` always, `report-to` too over https (Chrome sends nothing to an http Reporting API endpoint, and `report-to` silences `report-uri`). Zod runs `jitless` in the browser (`lib/zod-jitless.ts`), or its eval check trips the policy |
| API calls | `lib/agent.ts`, `app/api/threads/[id]/runs/route.ts` | Server-side calls to gen9-agent with the user's access token; chat answers stream (SSE) through a same-origin route |
| Connectors' apps | `components/chat/app-view.tsx`, `sandbox/server.ts`, `app/api/connectors/[id]/app/` | A connector tool's View (MCP Apps) runs under its step in a sandbox on another origin, one per connector (`MCP_APPS_SANDBOX_URL`, the `sandbox` service), under a CSP built from the domains it declared. The page speaks to it with the official host SDK (`AppBridge`, `@modelcontextprotocol/ext-apps`), and its tool calls and reads go through gen9-agent under the connector's policy. Its asks and messages can't take a decision the person didn't make: no focus taken from elsewhere, no yes within 500 ms of an ask appearing, no draft replaced without asking (`lib/app-asks.ts`, docs/design/screens/chat.md) |

Containers reach Keycloak over the `gen9-keycloak` network (`KEYCLOAK_INTERNAL_URL=http://gen9-keycloak:8080`) and gen9-agent over `gen9-agent` (`GEN9_AGENT_URL=http://gen9-agent:8000`). OIDC metadata is fetched there, and the issuer is checked against `KEYCLOAK_ISSUER`, the browser-facing URL.

## Configuration

| Variable | From | Purpose |
| --- | --- | --- |
| `KEYCLOAK_ISSUER`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` | `keycloak.local.env` | OIDC client |
| `SESSION_SECRET`, `VALKEY_PASSWORD` | `.env` (`init-env.sh`) | Session encryption (64 hex characters), session store |
| `SESSION_STORE_URL` | `compose.yaml`, or `.env` | The session store: the bundled Valkey, or another Valkey or Redis given by its URL, with `valkey` taken out of `COMPOSE_PROFILES` in `.env` ([docs/operations.md, "External services"](../docs/operations.md#external-services)). When it can't be reached, a request fails at once (sign-in: `/auth/error?reason=temporarily_unavailable`; `/api/health`: 503) and the next one tries again; a store that drops is reconnected in the background (`lib/auth/store.ts`, `reconnectDelay`) |
| `APP_URL`, `KEYCLOAK_INTERNAL_URL`, `GEN9_AGENT_URL` | `compose.yaml` | Per container (prod `:14000`, dev `:14001`) |
| `MCP_APPS_SANDBOX_URL` | `compose.yaml` | Where connectors' Views run: an origin template, `{id}` naming each connector (default `http://{id}.apps.localhost:14003`: Chrome resolves `*.localhost` to this machine, and it is another site than `localhost`). Unset, Views aren't shown |
| `PRIVACY_CONTROLLER`, `PRIVACY_CONTACT`, `PRIVACY_DPO`, `PRIVACY_AUTHORITY`, `PRIVACY_NOTICE_URL` | `.env` (optional) | The privacy page, `/privacy` (GDPR Art. 13), readable before signing up and linked from Settings and the sign-up page. The page states what Gen9 itself does: what it keeps and why, who else receives it (the model providers, search engines, email, connected services), for how long, and what a person can do. The organization running Gen9 fills in who it is and where to write (`PRIVACY_CONTROLLER`, `PRIVACY_CONTACT`), optionally its data protection officer and supervisory authority, or points to its own notice instead (`PRIVACY_NOTICE_URL`). Unset, the page says the organization hasn't named itself yet |

They are checked when the server starts (`instrumentation.ts`, `lib/env.ts`): one that is missing or wrong stops it, its log saying which and why (`[settings] SESSION_SECRET: Too small: …`), rather than a server that says it's ready while every request fails.

## Design system

Tokens, font and logo come from `gen9-design` (copies: `app/gen9-theme.css`, `app/fonts/`, `app/icon.svg`, `components/brand/logo.tsx`, `public/brand/`). Don't edit the copies. Change `gen9-design` and run `make design-sync`.

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile`, `Dockerfile.dev` | Production image (standalone output, non-root) and dev image |
| `compose.yaml` | `prod`, `sandbox`, `dev` (profile) and `valkey` (profile, in `.env`'s `COMPOSE_PROFILES`) services |
| `sandbox/server.ts` | The MCP Apps sandbox proxy (`node:http`, run from the prod image): one page, its CSP from the View's declared domains, framed by this app only |
| `init-env.sh` | Generates `.env` |
| `proxy.ts` | Optimistic route guard |
| `instrumentation.ts`, `instrumentation-node.ts` | At start: the settings checked, or the server stops saying what's wrong (Node.js only) |
| `lib/auth/` | OIDC client, sessions, cookies, CSRF origin check |
| `app/(app)/` | Signed-in screens (chat, search, scheduled, settings, admin) |
| `components/ui/` | shadcn/ui components (Base UI) |

## Ports

Published on `127.0.0.1` only.

| Port | Service | Variable |
| --- | --- | --- |
| `14000` | Production server | `GEN9_UI_PORT` |
| `14001` | Dev server + HMR | `GEN9_UI_DEV_PORT` |
| `14002` | Valkey (sessions) | `GEN9_UI_VALKEY_PORT` |
| `14003` | MCP Apps sandbox (any host name, e.g. `<connector>.apps.localhost`) | `GEN9_UI_SANDBOX_PORT` |

## Operate

| Task | Command |
| --- | --- |
| Status | `docker compose --profile dev ps` |
| Logs | `docker compose logs --tail=50 prod` |
| Rebuild prod | `docker compose up -d --build --wait` |
| Lint / types | `npm run lint && npx tsc --noEmit` |
| Unit tests | `npm test` (Vitest: auth, sessions and cookies, origin checks, what an approval shows, links, app asks, audit words, limits, and more) |
| Build | `npm run build`: the only check that refuses a server-only module (`lib/agent.ts`, `lib/env.ts`) imported into a client component; a failed image build leaves the old container running, so check `docker compose ps` shows it recreated |
| Sign everyone out of this app | Rotate `SESSION_SECRET` in `.env` (`./init-env.sh --force`), then `docker compose up -d`: old session records can no longer be decrypted |
| Stop everything | `docker compose --profile dev down` |

## Deploy on a VM

Put a TLS reverse proxy in front of `127.0.0.1:14000`, set `APP_URL=https://…` (cookies become `__Host-`, `Secure`, and pages send `Strict-Transport-Security: max-age=63072000; includeSubDomains`; checked behind a local TLS proxy, sign-in to sign-out), register the new redirect and post-logout URIs and the back-channel logout URL in Keycloak, and keep Valkey private. A chat's answer and a run waiting for you are server-sent events: the API pings every 15 s while one is quiet and responses say `X-Accel-Buffering: no`, so a proxy's usual idle timeout (nginx's `proxy_read_timeout`, 60 s) and buffering leave them open, given `proxy_http_version 1.1` (checked behind nginx 1.29: a run waiting 150 s, its stream never cut). For more than one instance, all instances share the Valkey session store.

Connectors' apps need the sandbox on a site of its own, never a subdomain of the app's: a wildcard
DNS name and certificate (for example `*.gen9-apps.example.net`) in front of `127.0.0.1:14003`,
with `MCP_APPS_SANDBOX_URL=https://{id}.gen9-apps.example.net` for the `prod` and `sandbox`
services. Leave it unset to keep apps off.

## Upgrade

1. Next.js: `npm install next@latest eslint-config-next@latest`, then rebuild both images.
2. Node.js image: pick the current LTS tag, get its digest with `docker buildx imagetools inspect node:<version>-slim`, and update `NODE_IMAGE` in both Dockerfiles (and gen9-keycloak's theme stage).
