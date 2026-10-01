# gen9-keycloak

[Keycloak](https://www.keycloak.org) 26.7.5 is the identity provider for Gen9: users, passwords, 2FA, sessions and tokens. It runs as an independent stack with its own Postgres and its own mail catcher ([Mailpit](https://mailpit.axllent.org)). Apps only need the issuer URL and their client credentials.

**Requires:** Docker with Compose v2, bash, openssl.

## Quick start

```bash
./init-env.sh --ui-env-file ../gen9-ui/keycloak.local.env --agent-env-file ../gen9-agent/keycloak.local.env
for n in gen9-keycloak gen9-ui; do docker network inspect $n >/dev/null 2>&1 || docker network create $n; done   # once; make up does it
docker compose up -d --build --wait     # healthy = realm "gen9" answers (≈40 s)
./verify.sh                             # independent checks, no app needed
```

| What | Where |
| --- | --- |
| Issuer | `http://localhost:15000/realms/gen9` |
| Admin console | http://localhost:15000/admin (user `admin`; password: `grep ^KC_BOOTSTRAP_ADMIN_PASSWORD= .env`) |
| Local users | `ada@gen9.test` (admin), `alan@gen9.test` (user); passwords: `grep ^GEN9_SEED_ .env`. Admins need a second step: Ada's authenticator code is `make admin-code` (from the repository's root) |
| Self-service account | http://localhost:15000/realms/gen9/account |
| Mail catcher (all Keycloak email) | http://localhost:15002 |

> [!IMPORTANT]
> The realm, including the client secret and seeded users, and the database password are applied **on first start only**. `init-env.sh` refuses to regenerate them once the data volume exists.

## Realm `gen9` (as code: `realm/gen9-realm.json`)

Imported on first start with `--import-realm`. `${VAR}` placeholders are filled from the container environment, which comes from `.env`.

| Area | Setting | Why |
| --- | --- | --- |
| Sign-up | Self-registration on; the email is the username; email must be verified | Users onboard themselves, and every account has a reachable, verified email |
| Passwords | `length(15) and maxLength(128) and notUsername and notEmail and passwordBlacklist(ncsc-100k.txt)` | [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/): 15+ characters for single-factor, at least 64 allowed, no composition rules, check a blocklist. The blocklist is the UK NCSC 100k list, pinned by checksum in the `Dockerfile` |
| Brute force | Temporary lockout after 5 failures, growing by 60 s up to 15 min | Keycloak's default `failureFactor` is 30 |
| Tokens | Access tokens last 5 min; refresh tokens rotate (`revokeRefreshToken`, reuse 0); SSO idle 30 min, max 10 h ("remember me": 14 d / 30 d); offline tokens at most 30 days from their sign-in, however often used (`offlineSessionMaxLifespan`); codes last 60 s | A stolen refresh token becomes useless once it has been used, and a replayed one ends that client's session. Keycloak's default keeps an offline token for as long as it's used (OWASP ASVS 5.0 10.4.8) |
| OAuth | No client allows the password or implicit grant, Keycloak's own `admin-cli` in this realm included; every client of the code flow needs PKCE with S256 but `temporal-ui`, every public client by client policy `public-clients` (`pkce-enforcer`); Gen9's clients may ask only for the scopes they use (`address`, `phone`, `organization` and `microprofile-jwt` off, `offline_access` only for agents). All set by `config/configure.sh`, checked by `verify.sh`, tried live by `e2e/oauth.mjs` | OAuth's security BCP forbids the password grant (RFC 9700, 2.4); ASVS 5.0 10.4.4, 10.4.6 and 10.4.11 (docs/plans/manual-e2e.md, P7-B1) |
| Audit | Login and admin events on, kept 30 days (`eventsExpiration`, and for admin events their own realm attribute `adminEventsExpiration`, which `configure.sh` sets on older realms: without it they stayed for good, a deleted person's email in each). Each is also a line of the container's log, successes too (`KC_SPI_EVENTS_LISTENER__JBOSS_LOGGING__SUCCESS_LEVEL=info`; the listener's default writes only failures), for an operator to send to a separate system: an admin who clears the stored events leaves no record of it ([docs/logging.md](../docs/logging.md#sending-the-logs-elsewhere)) | Traceability; what an account's deletion leaves here ends 30 days on Keycloak's `email` listener tells a person when how they sign in changes (`UPDATE_CREDENTIAL`, `REMOVE_CREDENTIAL`; `configure.sh`, `KC_SPI_EVENTS_LISTENER__EMAIL__INCLUDE_EVENTS`), in Gen9's words (the email theme's `event-*_credential.ftl`) |
| Email | SMTP → `mailpit:1025`, from `no-reply@gen9.test`; Gen9 email theme | Verify-email and reset-password work locally |
| Themes | `loginTheme` and `emailTheme` `gen9` | See [Gen9 theme](#gen9-theme-theme) |
| Roles | Realm roles `gen9-user` and `gen9-admin`, carried by groups `users` (default for new users) and `admins`. Seeded users also get `default-roles-gen9` (manage own account), like users who sign up | Roles go on groups, not on users. The import doesn't add default roles by itself, and Settings needs them to list and end the user's sessions |
| Second step | Authenticator app (TOTP), then recovery codes as the fallback (*Try another way*), set by `config/configure.sh` | Keycloak's built-in browser flow ships recovery codes disabled; the import can't change one step of a built-in flow |
| Forgot password | Email link, then the second step if the user has one (authenticator code, or a recovery code), then a new password: flow `gen9-reset-credentials`, built and bound by `config/configure.sh` | Keycloak's built-in reset flow has the user set up a new authenticator instead, so the email alone was enough to take over the account. `configure.sh` rebuilds the flow if it drifts from its definition |

Clients:

| Client | Type | Flows | Notes |
| --- | --- | --- | --- |
| `gen9-ui` | confidential | Authorization Code + PKCE (S256 enforced) | Exact redirect URIs `…/auth/callback`; back-channel logout → gen9-ui; audience mapper adds `gen9-agent` |
| `gen9-cli` | public | Device Authorization Grant ([RFC 8628](https://www.rfc-editor.org/rfc/rfc8628)) | Used by `gen9-cli`; audience `gen9-agent`; consent required, so users see which app they let in (against device-code phishing, RFC 8628 §5.4) |
| `gen9-agent` | confidential, no flows | — | Exists only as the `aud` of access tokens for the gen9-agent API |
| `temporal-ui` | confidential | Authorization Code, without PKCE: Temporal's UI sends none ([temporalio/ui#2519](https://github.com/temporalio/ui/issues/2519)); it checks the ID token's `nonce` before using any token, which RFC 9700 (2.1.1) allows a confidential client instead | Temporal's web UI (gen9-temporal). Signs in with its own browser flow, `gen9-temporal-ui`: the realm's sign-in (a Keycloak session, or the forms with their second step), then *Deny access* for anyone without `gen9-admin`, with Gen9's message: a message key, `gen9TemporalAdminsOnly`, which the theme words and titles ("Temporal is for admins", with *Back to Gen9*; `src/login/pages/Error.tsx`), and which `configure.sh` sets in place on an older install. The check follows the whole sign-in step, so a Keycloak session doesn't skip it (the Server Administration Guide's example, in the forms alone, does: keycloak/keycloak discussion #38350). Built, rebuilt on drift and bound by `config/configure.sh`; `verify.sh` checks it |
| `gen9-mcp` | public | Authorization Code + PKCE (S256 enforced) | For agents using Gen9 over MCP (gen9-agent `/mcp`) or A2A (`/a2a`), named "Agents (MCP and A2A)": loopback redirects on any port (`http://127.0.0.1/*`, `http://localhost/*`); consent required; default scope `gen9-mcp`, whose Audience mapper adds `GEN9_MCP_URL` (default `http://localhost:17000/mcp`); optional scope `gen9-a2a`, whose mapper adds `GEN9_A2A_URL` (default `http://localhost:17000/a2a`). Both are realm default optional scopes too, for self-registered clients. Created by `config/configure.sh` |
| MCP clients that register themselves | public (each a client of the realm) | Authorization Code + PKCE (S256 enforced, as for every public client, by client policy `public-clients`) | By the URL of their [Client ID Metadata Document](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00): Keycloak's experimental `cimd` feature, on at build time (`KC_FEATURES=cimd`). Client policy `mcp-clients` takes documents only from `GEN9_MCP_CLIENT_DOMAINS` (the redirect URIs' hosts must be there too), over http only if `GEN9_MCP_CLIENT_ALLOW_HTTP`. The development defaults are Claude's, VS Code's and the loopback hosts, plus `host.docker.internal` over http for the e2e; production allows https only. `gen9-mcp` is a realm default optional scope, so they get the audience by asking for it. Consent shows their name and host. `config/configure.sh` writes the realm's client policies whole |

## Gen9 theme (`theme/`)

Keycloak's pages (sign-in, sign-up, reset password, 2FA, verify email, update password, errors) and emails look like Gen9. The theme is built with [Keycloakify](https://docs.keycloakify.dev) 11 (React 19, Vite, Tailwind 4) from the `gen9-design` tokens, font and logo.

- **Template** (`src/login/Template.tsx`): the Gen9 frame. Mobile first: edge to edge on phones, a sheet on larger screens. It follows the device's light/dark setting and keeps Keycloakify's logic (messages, restart login, try another way, Keycloak's session scripts).
- **Pages:**
  - Sign-in, password reset, two-factor code, recovery codes (save, enter), *Add a passkey*, removing a sign-in method, choosing a password and info pages have Gen9 layouts (`src/login/pages/`). A new account's first password asks for *Password* and has no *Sign out of other devices*, which a reset, an admin's reset email and *Change password* keep: Keycloak tells them apart by its message (`updatePasswordMessage` only when the account itself carries UPDATE_PASSWORD, as sign-up leaves it), and a refused try, which comes back with its error instead, is still a first password for that sign-in's tab (`tab_id`, in sessionStorage). Every *Choose a password* says the rules under the field before the first try (`gen9PasswordHint`): Keycloak gives that page no password policy, so the theme words it, and `verify.sh` fails when its length differs from the realm's. Recovery codes are numbered everywhere (page, copy, download, print) because Keycloak accepts only the next unused one. *Add a passkey* names the passkey in a field on the page, pre-filled with the device and time (such as "Linux, 3 Mar 2030, 09:30"), instead of Keycloak's browser prompt: Keycloak rejects a name the user already has, and the stock default made every second passkey fail after the device had stored it.
  - Every other page uses Keycloakify's well-tested page logic, styled by a full class map (`src/login/classes.ts`).
- **Copy:** Gen9's voice via i18n overrides (`src/login/i18n.ts`), including server-rendered errors.
- **Emails:** `src/email/html/template.ftl` has a branded, table-based layout, with the mark set in type (`[9] gen9`, the 9 in lapis): mail clients don't reliably show an inline SVG or an embedded image. Copy is in `src/email/messages/messages_en.properties`.
- **Privacy:** every page's footer links to gen9-ui's `/privacy` (`GEN9_UI_URL`, a theme property Keycloak reads at runtime), except sign-up's, whose card links it instead: it says, before any data is collected, that Gen9 is an AI system and that what a person asks goes to AI model providers, some outside the EU, that don't train on it (GDPR Art. 13's first layer, WP260 rev.01).
- **Build:** the `Dockerfile` builds the jar in a Node + Maven stage and adds it before `kc.sh build`. The realm uses `loginTheme`/`emailTheme` `gen9`.
- **Preview without Keycloak:** `cd theme && npm install && npx vite --port 15010`, then open `http://localhost:15010/?page=register.ftl`, or any page id, with mock data.

**Sign-up flow:** with *verify email* on, Keycloak 26.7 asks only for email and name on the registration form. The user verifies the email first and then chooses a password (`RegistrationPassword`, `UPDATE_PASSWORD_AFTER_EMAIL_VERIFICATION`), which prevents account pre-hijacking.

## Hostname: one issuer for browsers and containers

- `KC_HOSTNAME=http://localhost:15000` makes the token `iss` always the browser URL.
- `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` makes the token, JWKS and userinfo endpoints follow the request host ([hostname guide](https://www.keycloak.org/server/hostname)).

Containers of other stacks therefore use `http://gen9-keycloak:8080` on the `gen9-keycloak` network, and tokens they receive still say `iss=http://localhost:15000/realms/gen9`. `verify.sh` checks both. Keycloak itself joins `gen9-ui` to call gen9-ui's back-channel logout at `http://gen9-ui:3000/auth/backchannel-logout`; `config/configure.sh` keeps that URL set on every start, including on realms imported before it changed.

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile` | Theme jar (Node + Maven stage), then the optimized production image (`kc.sh build` with Postgres, health, metrics, the theme) plus the password blocklist |
| `theme/` | Gen9 login and email theme (Keycloakify) |
| `compose.yaml` | `keycloak` (prod mode `start --optimized --import-realm`), `configure` (one-shot), `ready` (starts once `configure` succeeded, so `docker compose up --wait` returns only then), `postgres`, `mailpit` |
| `config/configure.sh` | Realm settings the import can't express, or that realms imported earlier must get too (the import skips an existing realm), applied with `kcadm.sh` after every start; idempotent. About 25 s: each `kcadm.sh` call starts a JVM, which `KC_OPTS` on this service alone (quick JIT, serial GC, a class-data archive the first call makes) brings from 0.99 to 0.58 s |
| `realm/gen9-realm.json` | The realm, as code |
| `init-env.sh` | Generates `.env` (secrets, seeded users) and, optionally, the app settings files. See `--help` |
| `verify.sh` | Independent checks (health, issuer, realm, configure step, users, clients, PKCE, redirect allow-list, password policy, email) |
| `.env` | Generated, secret, gitignored |

## Ports

| Port | Service | `.env` variable |
| --- | --- | --- |
| `15000` | Keycloak (login, OIDC, admin console) | `KEYCLOAK_PORT` |
| `15001` | Management: `/health/*`, `/metrics` | `KEYCLOAK_MANAGEMENT_PORT` |
| `15002` | Mailpit web UI + API, which ask for user `gen9` and `MAILPIT_UI_PASSWORD` from `.env` (it holds every email, password resets included, and every container on the `gen9-keycloak` network reaches it). It also catches gen9-agent's notification emails over SMTP (`gen9-mailpit:1025`, open) | `MAILPIT_PORT` |
| `15003` | Keycloak's Postgres | `KEYCLOAK_POSTGRES_PORT` |

## Manage users

- **Admin console:** http://localhost:15000/admin → realm **gen9** → *Users* / *Groups*. To make someone an admin, add them to group `admins`.
- **CLI** (inside the container):
  ```bash
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 --realm master --user admin --password "$(grep ^KC_BOOTSTRAP_ADMIN_PASSWORD= .env | cut -d= -f2)"
  docker compose exec keycloak /opt/keycloak/bin/kcadm.sh get users -r gen9 --fields username,enabled
  ```
- **Self-service:** users manage their password, 2FA, sessions and the apps they allowed (consents) from gen9-ui's Settings, or at http://localhost:15000/realms/gen9/account, Keycloak's own account console (unthemed, and linked from nowhere in Gen9).

## Change the realm after first start

Import runs only for realms that don't exist yet. Either:

- change it live in the admin console (or with `kcadm.sh`), and mirror the change in `realm/gen9-realm.json`, or in `config/configure.sh` for settings the import can't express (steps of built-in flows); or
- start over (**deletes all users**): `docker compose down -v && ./init-env.sh --force … && docker compose up -d --build --wait`.

## Deploy on a VM

Put TLS in front of `127.0.0.1:15000` (and rate-limit `/realms/gen9/device` there: [Upgrade](#upgrade)), generate `.env` with `--url https://id.example.com` and the public app URLs, and keep the management port (`15001`) private. For production, also replace the temporary bootstrap admin with a permanent one, and swap Mailpit for a real SMTP server in the realm's email settings.

## Upgrade

Read the [upgrading guide](https://www.keycloak.org/docs/latest/upgrading/). Update `KEYCLOAK_IMAGE` in the `Dockerfile` (the digest comes from `docker buildx imagetools inspect quay.io/keycloak/keycloak:<version>`), then run `docker compose up -d --build --wait && ./verify.sh`.

**Known findings in 26.7.5 (`make scan`, [docs/operations.md](../docs/operations.md#images-sboms-and-known-vulnerabilities)):** none with a fix. Two high ones without, in UBI 9's `pcre2` 10.40 (CVE-2026-86145, CVE-2026-89161): only Red Hat's rebuild of the base can bring their fix. An earlier Trivy run reported the bundled SQL Server driver (`mssql-jdbc`, CVE-2025-59250, high); the jar is `13.2.1.jre11`, the advisory's fixed version (GHSA-m494-w24q-6f7w), so that one was Trivy's reading of the version (docs/plans/manual-e2e.md, P6-D1). 26.7.5's Quarkus 3.33.4 brought `netty-handler` 4.1.138 and `bcprov-jdk18on` 1.86, which fix the critical findings of 26.7.4 (CVE-2026-75595, CVE-2026-8763, CVE-2026-13506), and FreeMarker 2.3.35 (CVE-2026-84939).

**In Keycloak itself:**

- CVE-2026-88770 ([#52783](https://github.com/keycloak/keycloak/issues/52783)), fixed in 26.7.5: the device grant, which `gen9 login` uses, issued tokens to an account locked for too many wrong passwords, to someone who still held a browser session of that account. `e2e/lockout.mjs` checks it: on 26.7.4 the terminal was signed in, on 26.7.5 it gets "Invalid user credentials".
- CVE-2026-94000 ([#53060](https://github.com/keycloak/keycloak/issues/53060)), fixed on Keycloak's main branch (26.8), not in 26.7.5: someone holding only `manage-users` can join a group that maps an admin role. Gen9's realm has no such group: its `admins` group maps `gen9-admin` and Temporal's roles (`gen9:admin`, `temporal-system:read`), none of Keycloak's own admin roles in `realm-management`. The one holder of `manage-users` is gen9-agent's service account, used only by gen9-agent.
- Guessing device codes ([#51275](https://github.com/keycloak/keycloak/issues/51275), open): the page where a person types the code from their terminal has no rate limit (30 wrong codes in 7 s, the same message each time). A guessed code signs the guesser's own account into the terminal that asked (RFC 8628, 5.1), and `gen9 login` says which account it signed in as. The codes are 8 letters from 20 (RFC 8628's recommendation, 20^8) and last 10 minutes. In production, rate-limit `GET /realms/gen9/device` at the proxy in front of Keycloak, as the RFC asks.
