# Gen9 auth architecture

Decisions for user management and authentication across the Gen9 stacks, with the evidence behind each one. Researched; versions are the latest stable releases on that date.

## Shape

```
 Browser ──(session cookie only)──▶ gen9-ui (Next.js BFF) ──(Bearer access token)──▶ gen9-agent (FastAPI)
    │                                   │   ▲                                            │
    │ login pages (themed)              │   │ back-channel logout                        │ JWKS (cached)
    ▼                                   ▼   │                                            ▼
 gen9-keycloak (Keycloak + own Postgres + Mailpit) ◀──────────────────────────────────────┘
                                                              gen9-agent ──▶ gen9-postgres (pgvector)
```

Every box is its own Docker Compose stack (own project, `.env`, volumes, network), as [docs/development.md, "How stacks stay decoupled"](development.md#how-stacks-stay-decoupled) requires. The host reaches stacks at published `127.0.0.1` ports (`localhost:<port>`); containers reach another stack over that stack's network, `gen9-<stack>:<container port>`, joining only the networks of the stacks they call ([docs/development.md, "How stacks stay decoupled"](development.md#how-stacks-stay-decoupled)).

| Stack           | Host ports (`127.0.0.1`)                                      |
| --------------- | ------------------------------------------------------------- |
| `gen9-ui`       | `14000` prod, `14001` dev, `14002` Valkey (sessions), `14003` connectors' app sandbox (its own origin) |
| `gen9-keycloak` | `15000` Keycloak, `15001` management (health/metrics), `15002` Mailpit UI, `15003` Keycloak Postgres |
| `gen9-postgres` | `16000` Postgres + pgvector                                    |
| `gen9-agent`    | `17000` API                                                    |

## Decisions

### 1. Keycloak is the only user store

Users, passwords, 2FA, sessions, email verification and password reset all live in Keycloak (26.7.5). Apps never see a password. gen9-ui and gen9-agent keep only the Keycloak subject id (`sub`) as a foreign key.

- Login, registration, reset, OTP and update-password screens are Keycloak-hosted pages with a Gen9 theme. The app never collects credentials, because the password grant is deprecated ([RFC 9700 §2.4](https://www.rfc-editor.org/rfc/rfc9700#section-2.4)).
- Roles go on groups, not users: `gen9-user` (everyone, via default group `users`) and `gen9-admin` (via group `admins`).
- The realm is kept as code: `gen9-keycloak/realm/gen9-realm.json` with `${ENV}` placeholders, imported on first boot (`--import-realm`). Docs say "If a realm already exists … the import operation is skipped" ([importExport](https://www.keycloak.org/server/importExport)), and `import --override` deletes the realm, users included, before importing (Keycloak 26.7.4 `ImportUtils`: "Removing it before import"). So what existing realms must also get (steps of flows, fixed URLs) is applied by `config/configure.sh`, the one-shot `configure` service that runs after every start. adorsys keycloak-config-cli, which would apply one realm file on every start, was rejected: its newest release (6.5.1, 2026-05-22) targets Keycloak 26.5.5; support for 26.7.4 is still an open pull request ([#1668](https://github.com/adorsys/keycloak-config-cli/pull/1668), checked). Moving the realm to it once released would retire both the import-only file and `configure.sh`.
- **A second step is offered to everyone, and required of admins.** A person signs in with a password alone until they set up an authenticator app or a passkey; from then on the browser flow asks for it (its second step is conditional on one being set up). OWASP ASVS 5.0 6.3.3 (L2) asks for multi-factor authentication, or a documented rationale with compensating controls. For members, the rationale: Gen9 is self-hosted by an organization that also runs its own sign-in policy, and a second step it can't recover would lock people out of their chats. The controls: a password of at least 15 characters, checked against the NCSC's 100,000 most common (`passwordBlacklist`), never the username or email (NIST SP 800-63B's length-over-complexity advice); lockout after 5 failed tries, growing by 60 s up to 15 minutes (`bruteForceProtected`); passkeys offered in Settings; sign-in records kept 30 days; a person's own sessions listed and ended in Settings. Admins are the accounts worth attacking, so they must have a second step, as GitLab's "Enforce two-factor authentication for administrators" and Nextcloud's group enforcement do. The realm's browser flow is Gen9's own, `gen9-browser` (Keycloak's built-in one takes no new step), built by `gen9-keycloak/config/configure.sh`: after the second step, a sub-flow for `gen9-admin` asks an admin who signed in with a password and passed no second step for an authenticator code, which has one set up before the sign-in ends (Keycloak's "Conditional 2FA sub-flow with OTP default"). A passkey is a second step already. Temporal's web UI has the same sub-flow in its own flow. Someone made admin while signed in keeps that session, which asked for no second step: Gen9's admin API signs them out when it makes them admin with none (the audit event says `signed_out`), and `configure.sh` signs out, at each start, any admin with none (made one in Keycloak's console). The seeded admin has an authenticator whose secret is `GEN9_SEED_ADMIN_OTP_SECRET` in `gen9-keycloak/.env`, which the checks answer and `make admin-code` prints the code of. Keycloak's own admin console (the master realm) is separate: its bootstrap admin is for setting up, as Keycloak says.
- **A person is told by email when how they sign in changes:** a password set, an authenticator app, a passkey or recovery codes added or removed, with when, from where, and what to do if it wasn't them (NIST SP 800-63B-4; [docs/logging.md, "Alerts"](logging.md#alerts-what-reaches-a-person-or-the-operator)). Not failed sign-ins.
- **Forgot password can't get past the second step.** Keycloak's built-in reset flow ends with *Reset OTP*: after the email link it has the user set up a new authenticator app ([docs](https://github.com/keycloak/keycloak/blob/main/docs/documentation/server_admin/topics/login-settings/forgot-password.adoc)), adding it next to the old one ([#8753](https://github.com/keycloak/keycloak/issues/8753)), so access to the email alone was enough to take over an account with an authenticator app (seen live; also raised upstream, unanswered: [#35663](https://github.com/keycloak/keycloak/discussions/35663)). The realm's reset flow is `gen9-reset-credentials`: email link, then, if the user has an authenticator app or recovery codes, its code or one recovery code, then the new password. A user who lost both needs an admin. OWASP lists single-use recovery codes first among ways to recover a second factor ([MFA cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html#resetting-mfa)).

### 2. gen9-ui is a Backend-for-Frontend (RFC 10017)

[RFC 10017](https://www.rfc-editor.org/info/rfc10017/) (Aug 2026) recommends a BFF for browser apps: the backend is a confidential client, runs Authorization Code + PKCE, keeps tokens server-side and gives the browser only a session cookie.

- **Library: [`openid-client`](https://github.com/panva/openid-client) 6.8.8.** It is OpenID Certified (Basic, FAPI 1.0, FAPI 2.0 RP profiles).
  - Better Auth was rejected. It keeps its own user table, which would duplicate Keycloak. Its database-less mode has open Keycloak bugs: [#6847](https://github.com/better-auth/better-auth/issues/6847) (state mismatch) and [#8287](https://github.com/better-auth/better-auth/discussions/8287) (refresh broken).
  - Auth.js was rejected: it is now maintained by the Better Auth team, and `next-auth@5` is still on the `beta` tag.
- **Sessions live server-side in Valkey 9**, inside the gen9-ui stack. The cookie holds only a random 256-bit id (`httpOnly`, `SameSite=Lax`; over https it is `__Host-gen9_session`, so `Secure` and pinned to this origin and path `/`, and the app sends HSTS). Server-side sessions make [OIDC Back-Channel Logout](https://openid.net/specs/openid-connect-backchannel-1_0.html) possible: Keycloak POSTs a logout token, and we delete every session with that `sid`. The endpoint reads at most 64 KiB of a body before it verifies anything. Valkey's memory is capped with `maxmemory-policy volatile-ttl`: anyone can start a sign-in, and each start stores a 10-minute record, so without a cap a flood grows memory by rate × 10 min. At the cap the keys closest to expiry (sign-in records) go before sessions; every key Gen9 writes has a TTL.
- **Next.js 16:** `proxy.ts` does only the optimistic cookie check. Real checks happen in a data-access layer next to the data, as the Next 16 docs recommend (`node_modules/next/dist/docs/01-app/02-guides/authentication.md`).
- Access tokens are refreshed server-side before they expire. Refresh token rotation is enforced by Keycloak (`revokeRefreshToken`).
- **Where you're signed in** (Settings) comes from Keycloak's Account REST API (`/account/sessions/devices`, what its own account console uses), called server-side with the user's access token: browser, system, IP and last activity per session. Signing a browser out uses the same API (`DELETE /account/sessions/{id}`, or all but the current one). Keycloak only touches the caller's own sessions, then notifies gen9-ui by back-channel logout. This needs Keycloak's default account roles, which every user has (seeded users get them explicitly, since the realm import doesn't add default roles).
- **Apps with access** (Settings) comes from the same API: the applications holding the person's consent (`/account/applications`: Gen9 CLI, "Agents (MCP and A2A)", clients registered by their metadata document), and *Remove access* revokes one (`DELETE /account/applications/{clientId}/consent`). Keycloak then ends the app's offline tokens and signs it out of the person's sessions, so its refresh token is refused (probed, and `e2e/mcp-server.mjs`); an access token it holds lasts out its 5 minutes. A person can't reach anyone else's consents this way, and gen9-agent needs no admin power for it.
- **Deleting the account** needs a real sign-in from the last 5 minutes, not just a live session. The BFF re-prompts with OIDC `max_age=0` (Keycloak asks for the password or passkey again), and gen9-agent checks the access token's `auth_time` itself, answering with RFC 9470's `insufficient_user_authentication` challenge. The deletion itself is a Temporal workflow (`DeleteAccountWorkflow`; see [docs/temporal.md](temporal.md) and gen9-agent's README, *Deletion*).:
  1. The Keycloak user is disabled and every session ends (Keycloak notifies gen9-ui by back-channel logout, so open tabs stop working at once), so nobody signs in while the rest runs.
  2. The user's Langfuse traces (prompts, answers) are submitted for deletion, following [Langfuse's procedure](https://langfuse.com/docs/administration/data-deletion): observations by `userId` over the account's lifetime (Observations API v2), their trace IDs, `DELETE /api/public/traces` in batches of 1,000. Langfuse deletes asynchronously.
  3. Their connectors' tokens are revoked at each server (RFC 7009), their environments (sandboxes) removed, and their scheduled tasks' Schedules removed.
  4. Gen9's data goes: threads, LangGraph checkpoints, the person's memory, the user row, the model router's records of the user (usage and cost, through gen9-models' admin API), then the runs' workflows in Temporal.
  5. The Keycloak user is deleted.
  6. Traces and the router's records are erased again after 1 and 10 minutes, for any still being written (Langfuse takes seconds; the router writes daily totals in batches).

  The deletion is in Gen9's audit record (`account.delete`), which a restore reads to delete it again ([docs/operations.md, "Back up and restore"](operations.md#back-up-and-restore)).

  A step that fails, such as Langfuse being unreachable, is retried by the workflow until it succeeds, so nobody has to retry by hand. The API answers `204` once the data is gone, or `202` if that takes longer than 15 s; the web app then says the account is being deleted, not that it was (`/signed-out?reason=deleting`; for an admin, a toast saying the user shows as disabled until it's done). Admins can delete other users the same way from *Users* (same recent sign-in rule). The only admin can't delete themselves.

  Users deleted in Keycloak directly (its admin console) never pass through Gen9. A Temporal Schedule (`sweep-deleted-users`, every 15 minutes) finds users Gen9 knows that are missing from Keycloak's user list and whose own lookup returns 404, and removes each one's traces, chats and row with the same workflow. The per-user 404 check means an incomplete listing can't cause a deletion. Keycloak doesn't notify apps when it deletes a user; their open tab lasts until the access token expires (5 min) and the refresh fails.
- **Logout** is RP-initiated through Keycloak's `end_session_endpoint`, with `id_token_hint` and a registered `post_logout_redirect_uri`. If the app session has already expired, sign-out still goes to Keycloak, which asks the user to confirm (themed page) and then ends its SSO session. Otherwise the next *Sign in* on a shared computer would skip the password.

### 3. gen9-agent is a resource server

It is a FastAPI service that validates Keycloak access tokens locally:
- JWKS is cached by PyJWT's `PyJWKClient` (5 minutes) and refetched on an unknown `kid`, at most
  once per 30 s (PyJWT 2.15's cooldown). So a signing key is rotated by publishing the new one
  passive first (docs/secrets.md).
- The RS256 signature, `iss`, `aud=gen9-agent` and `exp` are checked, `nbf` when present, and `exp`, `iat`, `iss`, `aud` and `sub` must be there; the token must be an access token (`typ: Bearer`) from an allowed client (`azp`).
- Roles come from `realm_access.roles`.

Keycloak's default `aud` is only `account` (verified on 26.7.4), so the `gen9-ui` client has an audience mapper that adds `gen9-agent`.

- Users are provisioned just-in-time from token claims into `users`, keyed by `sub`.
- Data lives in gen9-postgres: async SQLAlchemy 2.0 over psycopg 3, with an Alembic schema. LangGraph's Postgres checkpointer persists agent threads.

### 4. Issuer that works from both the browser and containers

The browser uses `http://localhost:15000`, and containers use `http://gen9-keycloak:8080`. Keycloak is started with:
- `KC_HOSTNAME=http://localhost:15000`, so `iss` is always the browser URL;
- `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`, so token, JWKS and userinfo endpoints follow the request host ([hostname guide](https://www.keycloak.org/server/hostname)).

### 5. Design system

- **shadcn/ui on Base UI.** It has been the shadcn default since July 2026: Base UI 1.x is stable with 6M+ weekly downloads, and Radix remains supported ([changelog](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default)).
- **Styling:** Tailwind CSS 4 with semantic CSS-variable tokens in OKLCH, light and dark.
- **Tokens:** `gen9-design/theme.css` is the single source; `make design-sync` copies it, with the font, the logo and the icons, into gen9-ui and the Keycloak theme (`gen9-design/sync.sh`), and `make design-check` fails on a copy that differs.
- **Keycloak theme:** [Keycloakify](https://docs.keycloakify.dev/) 11.16 (React). It is built into a JAR inside the Keycloak image build, so both login and app screens use the same tokens and components.

### 6. Local email

Mailpit, inside gen9-keycloak, catches all Keycloak mail, so verify-email and reset-password flows can be tested end to end without a real SMTP server.

### 7. Temporal takes Keycloak tokens too

gen9-temporal checks the same realm's tokens (details in
[docs/temporal.md](temporal.md), "Security"):
- **Roles** are client roles of a bearer-only client `temporal`, named as Temporal's default claim
  mapper reads them, `<namespace>:<role>`. A mapper puts them in a `permissions` claim; Keycloak
  then adds `temporal` to `aud` only for users who hold one (its "audience resolve" mapper, in the
  realm's default `roles` scope, adds each client whose roles a user holds).
- **gen9-agent** gets `gen9:write` for its service account; its Temporal client sends that token.
- **Admins** (group `admins`) get `gen9:admin` and `temporal-system:read`.
- **Temporal's web UI** signs in with its own confidential client, `temporal-ui` (Authorization
  Code). Its server discovers Keycloak at `gen9-keycloak:8080` and expects the browser issuer
  (`TEMPORAL_AUTH_ISSUER_URL`), as decision 4 requires.
- **gen9-agent's codec endpoint** decodes payloads for the UI. It accepts only tokens from
  `temporal-ui` with audience `temporal` that grant `gen9:admin`.
- **Only admins get in.** The web UI's client signs in through a flow of its own,
  `gen9-temporal-ui`, which ends in Keycloak's *Deny access* for anyone without `gen9-admin`,
  on a Gen9 page that says why and links back (`e2e/temporal.mjs`).

### 8. Agents get tokens for the endpoint they call: MCP or A2A

Gen9 is an MCP server at `gen9-agent`'s `/mcp` (gen9-agent/README.md, "MCP server"; researched). MCP 2026-07-28 requires Protected Resource Metadata (RFC 9728) and tokens issued
for the server itself.
- **Audience.** Keycloak ignores RFC 8707's `resource` parameter, as its "Integrating with
  Model Context Protocol" guide says. So the `gen9-mcp` client scope carries an Audience mapper
  that adds `GEN9_MCP_URL`. The server takes only tokens with that audience and the `gen9-mcp`
  scope. It refuses the API's tokens (audience `gen9-agent`), and the API refuses its tokens.
- **Client.** MCP clients sign in as the pre-registered public client `gen9-mcp`: authorization
  code with PKCE (S256 enforced), loopback redirects on any port (RFC 8252), consent required
  (the screen says "Use Gen9 from this app"), and `gen9-mcp` as a default scope.
  `config/configure.sh` creates the scope and the client on every start.
- **A2A.** Gen9 as an A2A agent (`/a2a`, gen9-agent/README.md, "A2A") binds its tokens the
  same way. The `gen9-a2a` scope's Audience mapper adds `GEN9_A2A_URL`, its Agent Card declares
  the authorization code flow with that scope, and the same public client asks for it. The
  endpoint takes any client of the realm (`allowed_clients` None), so self-registered clients
  work too. It requires the scope and the audience, and refuses the API's tokens. A client
  reaches only the chats it started (`threads.a2a_client`, the token's `azp`): agents that share
  the pre-registered client share its chats, and one registered by its own document has its own.
- **Self-registered clients.** Clients may also register themselves by the URL of their Client
  ID Metadata Document, which is the spec's recommendation. This uses Keycloak's experimental
  `cimd` feature, enabled at build time. A client policy lets documents in only from
  `GEN9_MCP_CLIENT_DOMAINS`, and the redirect URIs' hosts must be there too. It allows http
  only if `GEN9_MCP_CLIENT_ALLOW_HTTP` says so, as it does in development. Keycloak keeps each
  document's client (its issue #45284).

### 9. Work for a person ends with their access

Scheduled tasks, API triggers, background tasks and queued runs act for a person who isn't
there, with gen9-agent's own identity (researched; plan, "Auth across the harness").
Nothing in that path would notice that an admin disabled the person, or that they were deleted.
A probe showed a disabled person's trigger firing and its run answering. In Microsoft Entra
Agent ID's model, delegated background access lasts only while the person's does. So a firing,
a trigger, a run's start and a notice each ask Keycloak whether the person is enabled
(`standing.py`). The answer is kept for a minute per process, and a Keycloak that doesn't answer
makes the work retry rather than go ahead. Keycloak is the source of truth, so disabling in its
console counts as much as disabling in Gen9. `e2e/standing.mjs` checks each path.

A turn under way asks too, before each model call, in the agent and every subagent
(`StillActive`): a person disabled or deleted mid-turn stops within that minute. An admin's
*Disable* in Gen9 also stops the person's queued, running and waiting runs at once, and counts
them in its audit event (`e2e/stop.mjs`; plan, P5-C10).

### 10. Gen9 keeps its own record of who did what

Admin actions reach Keycloak as gen9-agent's service account, so Keycloak's admin events name the
service, not the admin (seen live in `e2e/audit.mjs`). OWASP ASVS 5.0 V16 wants the actor in
every security event, and failed authorization logged. So `audit_events` records admin actions,
people's security actions and refused access, with the human actor. It never holds a secret, and
it is append-only by trigger (gen9-agent/README.md, "How auth works"). Keycloak keeps its own
login and admin events for 30 days (`eventsExpiration`, and `adminEventsExpiration` set by
`configure.sh`).

### 11. The terminal is never an admin

`gen9-cli` signs in with the device grant, and its consent grants `openid email profile`, so its
tokens carry no realm roles: admin routes answer it 403. That's the rule, not a gap. A CLI keeps a refresh token on disk for days, and admin power in it would last as
long. The web app asks for a fresh sign-in before deleting people (`max_age=0`), which a terminal
can't. Admins act in the web app, and every admin action is in the audit record (decision 10).
Every secret, and how to replace it: [secrets.md](secrets.md).

### 12. What each shared network reaches

Stacks meet only on `gen9-<stack>` networks ([docs/development.md, "How stacks stay decoupled"](development.md#how-stacks-stay-decoupled)). Checked with `docker network inspect` and a probe from a container on each:

| Network | Members | What a member reaches, and how it's guarded |
| --- | --- | --- |
| `gen9-postgres` | Postgres; gen9-agent's API, worker and migrate job | The app database, by password: the API and worker as a role that owns nothing, the migrate job as the owner (decision 13) |
| `gen9-keycloak` | Keycloak, Mailpit; gen9-ui, Temporal and its UI, gen9-agent's API and worker | Keycloak's token and admin endpoints (client credentials); Mailpit's SMTP (open, to send), and its UI and API, which ask for a password |
| `gen9-langfuse` | Langfuse; gen9-agent's worker | Langfuse's API, by project keys (the API left this network) |
| `gen9-temporal` | Temporal; gen9-agent's API and worker | Temporal's frontend, by Keycloak tokens with Temporal roles |
| `gen9-models` | The router and its admin API; gen9-agent's API and worker | Models by virtual key (the API's may only embed and rerank); the admin API by gen9-agent's keys (the API's may only read usage) |
| `gen9-sandbox` | OpenSandbox; gen9-agent's worker | The sandbox server, by API key; sandboxes' `execd` is bound to loopback on Docker Desktop, and to the Docker bridge's gateway on Linux (gen9-sandbox/README.md) |
| `gen9-agent` | gen9-agent's API; gen9-ui | The API, by access tokens |
| `gen9-ui` | gen9-ui; Keycloak | The back-channel logout endpoint, by a signed logout token |

The probe found one hole: Mailpit's API answered 200, with every email, to gen9-ui's container
without credentials. Password-reset links included, so any one compromised service could have
taken over any account. Mailpit's UI and API now ask for a password (`MAILPIT_UI_PASSWORD`, user
`gen9`), and the same probe gets 401. SMTP stays open to the senders. In production, a real SMTP
server replaces Mailpit.

Every route's authorization is checked the same way, from the API's own OpenAPI document
(`e2e/authz.mjs`). There were no holes: 401 without a token, 403 for non-admins on admin routes,
and 403 or 404 for another person's ids.

### 13. The services own nothing in the database

At first, gen9-agent's API, worker and migrate job all connected as `gen9_agent`, the owner
of its database, its tables and the audit trigger's function. An owner may alter, disable or
replace any of them, and PostgreSQL doesn't let that right be revoked, so whoever held the API's
database password could have switched the audit record's protection off. OWASP ASVS 5.0 13.2.2
asks for the least privilege between backend components, and OWASP's Database Security Cheat
Sheet says the application's account "should not be the owner of the database".

Now the owner is the migrate job's alone. The API and worker connect as `gen9_agent_app`, which
gen9-postgres creates on every start: it reads and writes rows, may only add to `audit_events`,
and can't alter, drop or create anything, set another role or skip triggers (`e2e/audit.mjs`
tries each from inside both containers). The one piece of DDL the worker needs, a search model's
index, goes through two functions the owner defines, which check their inputs and build or drop
only those indexes (gen9-agent/README.md, "Search"; `e2e/search.mjs` rebuilds one).

### 14. Admin access is checked when it's used

A token carries the roles it was issued with for its 5 minutes, and gen9-agent checks tokens
locally. Quinn, whose admin access Ada had removed, still saw Users,
Plugins and the Audit log until their token was refreshed 3 min 53 s later, and the API would have
done what they asked. OWASP ASVS 5.0 8.3.2 asks that authorization changes apply immediately.

Now every admin route asks Keycloak for the caller's realm roles (theirs and their groups') after
the token's own check: one Admin API call as gen9-agent's service account, which admin pages
make a few of. If Keycloak can't answer, the request gets `503`, never the benefit of the doubt.
An admin page whose API call is refused this way says "You need admin access", as it does for
anyone who isn't an admin; the sidebar's Admin entries follow at the next refresh. Granting works
the other way round: a new admin sees the entries after their next refresh (up to 5 minutes).

## Verified live

Everything below was run against the running stacks. Browser flows were driven in Chrome with real local users and passwords, and TOTP codes were computed from the secret Keycloak displayed.

| Flow | Result |
| --- | --- |
| Keycloak on its own (`gen9-keycloak/verify.sh`) | 34/34 checks (sign-in: Gen9's flow bound, its second step, admins need one, the seeded admin's authenticator app; forgot password: Gen9's flow bound, second step before the new password): health, issuer from host and container, realm, themes, passkeys, the configure step (recovery codes after the authenticator app), roles, seeded users manage their own account, PKCE enforced, redirect allow-list, `gen9-cli` device-only with consent, NIST password policy, branded email to Mailpit |
| Sign in (Authorization Code + PKCE) through the Gen9 theme | Session in Valkey (sealed, TTL = SSO idle), ID-token name shown in the app |
| Sign up → branded verify email → set password → sign in | Works; new users get `gen9-user` through the default group |
| Forgot password → branded email → new password → signed in | Works; the reply never reveals whether an account exists |
| Forgot password with an authenticator app (`make e2e`, `e2e/recovery.mjs`) | The email link asks for the authenticator code first; a wrong code is refused; the right one leads to a new password; still one authenticator, none set up during the reset. With Keycloak's built-in flow bound instead, the link offers to set up a new authenticator and the check fails |
| Change password (application-initiated action) | Re-authentication, blocklisted password rejected, new one accepted, returns with confirmation |
| Authenticator app setup + sign-in with a TOTP code | Wrong code rejected; valid code signs in |
| Admins need a second step (`e2e/stacks.mjs`, `demotion.mjs`, `audit.mjs`, `passkeys.mjs`, `temporal.mjs`) | The seeded admin is asked for the code and signs in with it; an admin with none sets up an authenticator app at that sign-in, then is asked for its code; made admin in the web app without one, a person is signed out everywhere (their terminal too), recorded as `signed_out`; an admin signing in by passkey is asked nothing more; Temporal's web UI asks the admin for the code and still turns a member away |
| Brute force | 5 failures → temporary lock (even the right password refused) → lock expires; admin *Unlock sign-in* clears it (audited as an admin event) |
| Sign out (RP-initiated) | App session deleted; Keycloak `LOGOUT` event without a confirmation prompt. With the app session already expired: Keycloak confirmation page, then 0 Keycloak sessions and the next sign-in asks for the password |
| Back-channel logout | Admin ends a user's sessions → Keycloak POSTs a logout token → gen9-ui drops the session → next request signs in again |
| Sign out everywhere | 0 Keycloak sessions, 0 app sessions |
| gen9-agent authorization | 401 without or with a bad token (RFC 6750 challenge), 401 for ID tokens, 403 `insufficient_scope` for non-admins, 404 for other users' threads, admin cannot demote themselves |
| Chat | Streamed answers with sources through the BFF (SSE), threads persisted in Postgres |
| Admin user management | List, grant/revoke admin, enable/disable (disable also ends sessions), password reset email, unlock |
| Mobile (390×844), light and dark | Landing, app shell and drawer, settings, Keycloak pages. Rechecked after the later features in Chrome with touch viewports of 320, 360 and 390 px: Settings (sessions with *Gen9 CLI*, delete account), the delete-account and delete-user dialogs and the CLI consent page have no horizontal overflow and stack their actions full-width. Production gen9-ui refuses to be framed (clickjacking headers), so phone checks drive Chrome directly |
| Silent token refresh | Access token refreshed server-side before expiry (Keycloak session `lastAccess` moves; page loads with the new token) |
| Content-Security-Policy | Per-request nonce, strict-dynamic: no violations; hydration, streaming and sign-out work |
| Passkeys (`make e2e`, Chrome with a virtual platform authenticator) | Added from Settings on Gen9's page without a browser dialog; then signed in by passkey autofill and by the *Sign in with a passkey* button, no password; Keycloak logs both as `webauthn-passwordless` with user verification. A second passkey no longer fails on a duplicate name. Removed from Settings after Keycloak's confirmation (`REMOVE_CREDENTIAL`) |
| Recovery codes | Created from Settings on Gen9's page (12 codes, numbered; copy, download, print); sign-in with password → authenticator app → *Try another way* → *Recovery code* → code 1 (typed with hyphens) signs in; code 2 out of order rejected (`LOGIN_ERROR`, counts toward lockout); Settings shows 11 of 12 left. Removing the last authenticator app removes the codes too (BFF callback → gen9-agent → Keycloak, audited as the gen9-agent service account), so the next sign-in asks only for the password |
| Delete account | A new account (sign-up, verified email, password, one chat) deleted from Settings: Keycloak user, agent rows, thread and all LangGraph checkpoint rows gone (deleted as gen9-agent's service account); the old password is refused. With a sign-in older than 5 min the dialog sends the user to sign in again (Keycloak re-auth page via `max_age=0`), then reopens; the agent alone also refuses such a token (401 `insufficient_user_authentication`). The only admin gets "Make someone else an admin first". Langfuse: the user's trace (all 4 observations) gone 15 s after deleting the account; with Langfuse stopped, deletion stops with "Couldn't delete your chat history…", nothing is deleted, and a retry after Langfuse is back completes. (deletion on Temporal), with Langfuse stopped a chat's deletion answers 202 with the chat already hidden, and finishes on its own 35 s after Langfuse is back |
| Admin deletes a user | Mary signed in and chatted in her own browser; Ada (admin) deleted her from *Users* after typing her email: Keycloak user, agent row, checkpoints and her Langfuse trace gone; back-channel logout removed her Gen9 session and her open tab was sent to sign in |
| Sign-in flood | 2,000 unauthenticated `/auth/login` requests added 2,000 keys (about 390 bytes each, 10 min TTL). With a deliberately tiny cap (+400 KB) and `volatile-ttl`, 5,000 more stayed under the cap (3,979 evicted), Ada's session survived, and a real sign-in in Chrome still worked |
| User deleted in Keycloak's admin console | Annie signed in and chatted; deleted in the console (Chrome, bootstrap admin): Gen9's data stayed at first, no back-channel logout. The next sweep (60 s for the test) removed her row, checkpoints and Langfuse trace, plus two stale Ada rows from earlier Keycloak rebuilds; the live Ada row was kept. Her open tab was sent to sign in once its token expired |
| Dependencies and images | `make audit`: 0 known vulnerabilities in gen9-ui, the Keycloak theme, e2e (npm) and gen9-agent's 338 locked Python packages (pip-audit; a known-vulnerable pin makes it fail). Docker Scout: gen9-agent 0 fixable; gen9-ui from 2 critical / 13 high to 0 / 2 (none fixable) by moving to the Debian 13 (trixie) Node image and removing the unused npm CLI from the runtime image; gen9-keycloak on 26.7.5 (Trivy): 1 high, in the SQL Server driver bundled with Keycloak, which Gen9 never loads; 26.7.4's 4 critical and 9 high are fixed (see gen9-keycloak/README) |
| Terminal sign-in (`gen9-cli`, device flow) | `gen9 login` printed a code; confirming it in Chrome asked for Ada's password, then *Allow Gen9 CLI to use your account?*; the terminal got its tokens (file mode 0600) and `gen9 ask` streamed a sourced answer. An expired access token was refreshed and the rotated refresh token saved. The web app then joined the same Keycloak session by single sign-on; `gen9 logout` revoked only the CLI (the session kept `gen9-ui`, the revoked refresh token got `invalid_grant`). *Don't allow* ended the terminal with "Sign-in was denied." |
| Terminal sessions in Settings | A CLI approved in the same browser shows on that row ("Gen9 CLI signed in through it too"); one approved elsewhere gets its own row, *Gen9 CLI, approved in HeadlessChrome on macOS*. *Sign out* on it ended its Keycloak session: its next refresh was refused and it deleted its credentials, while the other CLI kept working. The terminal's current access token stays valid until it expires (at most 5 minutes), since gen9-agent checks tokens locally |
| Where you're signed in | Settings lists each browser ("Chrome on macOS", "Edge on Windows"), IP and last activity; this one is marked. *Sign out* on another browser: Keycloak `LOGOUT` via the account API, back-channel logout drops its Gen9 session, and that browser's next page load asks it to sign in again. *Sign out other browsers* ended 15 sessions and kept this one |
| Accessibility (`make e2e`, axe-core, WCAG 2.2 A/AA) | 8 screens (landing, sign-in, sign-up, reset password, signed out, chat, settings, users) × desktop and phone × light and dark: no violations. The audit found a real bug: Keycloak pages set dark mode after React rendered, so dark-mode users saw a light page first. An inline script now sets it before the first paint |
| Remove an authenticator app | Settings lists it; *Remove* opens Keycloak's confirmation (`kc_action=delete_credential:<id>`, Gen9 theme). Cancel keeps it (`rejected_by_user`), Remove deletes it (`REMOVE_CREDENTIAL`); the password can't be removed |
| Clean-room setup | All new stacks and generated `.env` files deleted, then the README's *First-time setup* + `make up`: every stack healthy in about a minute, `verify.sh` 21/21, sign-in and admin page work. Repeated for gen9-keycloak after adding the `configure` step: fresh realm healthy in 23 s, recovery-code step switched on, `verify.sh` 23/23, `make e2e` passes. Repeated for all four stacks after the CLI, sweeper and Valkey changes: `make up` healthy in 67 s, `verify.sh` 26/26, `make e2e` and `make audit` pass, unit tests pass (UI 32, agent 21, CLI 6), sign-in with the regenerated password and a chat work in Chrome, and `gen9 login` (consent from the imported realm) / `ask` / `logout` work |

Passkeys are verified with Chrome's virtual authenticator, because automation can't drive Touch ID or Windows Hello. That authenticator implements the same WebAuthn API a real device answers, so only the OS dialog itself is untested.
