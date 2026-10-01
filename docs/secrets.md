# Secrets, and how to replace each

Every secret Gen9 keeps, which files hold it, who uses it, and how to replace it without losing
data (docs/plans/harness.md, "Auth across the harness"). What each key does, with which algorithm,
is in [cryptography.md](cryptography.md). `make setup` generates them all, and
never prints them: read key names, never values (root AGENTS.md). After a change, `make up
STACKS="…"` restarts what reads it.

`make backup` copies every settings file with the data ([docs/operations.md, "Back up and restore"](operations.md#back-up-and-restore)): the data is
encrypted with these keys, so a backup is as secret as the files themselves, and `make restore` puts
them back with it.

Two rules hold throughout:
- **Key rings rotate without downtime.** The first key seals or encrypts, and every key in the ring
  opens. Put a new key first, move what the old key sealed, then remove the old key.
- **A secret that two stacks share changes in both.** The owning stack's `.env` holds it, and
  `init-env.sh --from-env --force` writes it again into the other stack's `*.local.env`.

## How often to replace them

A schedule, as OWASP ASVS 5.0 (13.1.4) asks, from NIST SP 800-57 Part 1 Rev. 5's suggested
cryptoperiods (Table 1) for each kind of key. Replace sooner when a secret may have leaked, or when
someone who held it leaves. Each row's "To replace" below says how.

| Kind | Secrets | At least every | NIST's suggestion |
| --- | --- | --- | --- |
| Keys that encrypt data | `GEN9_SECRET_KEYS`, `TEMPORAL_PAYLOAD_KEYS` | 2 years for the key that seals; an old one stays only while something sealed with it is left | Symmetric data encryption: up to 2 years to encrypt, 3 more to decrypt |
| A key that keys are derived from | `SESSION_SECRET` | Year | Master or key-derivation key: about 1 year |
| Keys that sign | Keycloak's realm signing key (RS256); Temporal's internode CA and certificate, made together by `init-tls.sh` (its certificate lasts 825 days) | 2 years | Private signature key: 1 to 3 years |
| Secrets that prove who's calling | Client secrets, the router's keys, `SANDBOX_API_KEY`, database and Valkey passwords, Langfuse's project keys, `MAILPIT_UI_PASSWORD`, Keycloak's HS512 key | Year | Symmetric authentication key: under 2 years |
| Other people's keys | Provider keys | As the provider says, and when someone who held one leaves | |
| Never | `LITELLM_SALT_KEY` | A new one can't read what's stored (its row below) | |

## Key rings

| Secret | Where | Used by | To replace |
| --- | --- | --- | --- |
| `GEN9_SECRET_KEYS` (the vault) | `gen9-agent/.env` | API, worker: people's connector tokens, sign-ins and environment secrets, sealed in Postgres (`vault.py`) | 1. Put a new `id:<32 bytes base64>` first and restart gen9-agent. 2. Run `docker exec gen9-agent-worker-1 gen9-agent-reseal`, which seals every value again with the new key, bound to the same owner and row. 3. Once `gen9-agent-reseal --check` exits 0, remove the old key and restart. 4. `gen9-agent-reseal --verify` opens every value with the keys left. **Run live:** `k1` to `k2`, a connector token, an environment secret and a sign-in moved, and all opened after the old key was removed; **again**, `k3` to `k3b`, after which an environment's call to a secret's host still carried its bearer token |
| Keycloak's realm signing keys (RS256, the realm's `rsa-generated` key providers) | Keycloak's database (gen9-keycloak's volume) | Keycloak signs every token with the active one; gen9-agent verifies access tokens with them (its key set cached 5 minutes) and gen9-ui back-channel logout tokens (cached 10 minutes, jose's default) | 1. Realm settings, *Keys*, *Providers*: add an `rsa-generated` provider at a higher priority, **Active off**: published, not yet signing. 2. Wait at least 10 minutes, so every verifier has the new key cached. 3. Turn it active: new tokens are signed with it, and the old key, now passive, still verifies the ones already issued. 4. Once those have lapsed (Keycloak suggests one to two months, for cookies too), delete the old provider. Made active at once instead, a new key is refused for up to 30 s: PyJWT refetches a key set for an unknown `kid` at most once per 30 s (2.15's cooldown), as jose does for the logout tokens, so for that long a person's requests get 401 and an admin's sign-out could miss a web session. **Run live:** made active at once, a new token got 401 until the cooldown passed; published passive first, then activated, accepted at once |
| `TEMPORAL_PAYLOAD_KEYS` | `gen9-agent/.env` | API, worker, Temporal UI's codec endpoint: what Gen9 sends through Temporal (`codec.py`) | Put a new key first and restart gen9-agent. Workflow histories can't be encrypted again: keep the old key until the namespace's retention has passed for everything it encrypted (docs/temporal.md, "Security"), then remove it |

## Single keys and passwords

| Secret | Where | Used by | To replace |
| --- | --- | --- | --- |
| Keycloak: `GEN9_UI_CLIENT_SECRET`, `GEN9_AGENT_CLIENT_SECRET` | `gen9-keycloak/.env`; copies in `gen9-ui/keycloak.local.env`, `gen9-agent/keycloak.local.env` | gen9-ui (sign-in, the BFF), gen9-agent (its service account: users, Temporal) | Set it in Keycloak first (`kcadm update clients/<id> -r gen9 -s secret=…`, or *Regenerate* in the admin console), then in `gen9-keycloak/.env`. Rewrite the copies with `gen9-keycloak/init-env.sh --from-env --force` and the consumer's file flag, and restart gen9-ui and gen9-agent. The realm import sets these only on a first start |
| Keycloak: `GEN9_TEMPORAL_UI_CLIENT_SECRET` | `gen9-keycloak/.env`; `gen9-temporal/keycloak.local.env` | Temporal's web UI | Change it in `gen9-keycloak/.env`: `configure.sh` sets it in Keycloak on every start. Rewrite gen9-temporal's copy, and restart both |
| `KC_BOOTSTRAP_ADMIN_PASSWORD`, seeded users' passwords | `gen9-keycloak/.env` | Keycloak's master admin; the seeded users of the checks | Change the password in Keycloak, then in `.env`. The checks read `.env` |
| `GEN9_SEED_ADMIN_OTP_SECRET` | `gen9-keycloak/.env` | The seeded admin's authenticator app (admins need a second step): `configure.sh` gives it to them when they have none; the checks and `make admin-code` answer it | Remove the seeded admin's authenticator app in Keycloak, put a new value in `.env` (`openssl rand -hex 20`) and `make up STACKS=keycloak`: `configure.sh` gives them the new one |
| `MAILPIT_UI_PASSWORD` | `gen9-keycloak/.env` | Mailpit's web UI and API (user `gen9`); the checks that read emails | Change it in `.env` and restart gen9-keycloak: Mailpit's password file is written from it at start |
| `SESSION_SECRET` | `gen9-ui/.env` | The BFF: sessions in Valkey are sealed with a key derived from it | Replace it with 64 hex characters (`openssl rand -hex 32`) and restart gen9-ui; a shorter one stops it at start, saying so in its log. Every web session becomes unreadable, so everyone signs in again (Keycloak's own session makes that quick) |
| `VALKEY_PASSWORD` | `gen9-ui/.env` | gen9-ui and its Valkey | Replace it and restart gen9-ui (sessions are lost, as above) |
| Router: `LITELLM_MASTER_KEY` | `gen9-models/.env` | LiteLLM's admin API: the keys job and nothing else | Replace it and restart gen9-models |
| Router: `GEN9_AGENT_MODELS_KEY`, `GEN9_AGENT_API_MODELS_KEY`, `GEN9_EVALS_MODELS_KEY` | `gen9-models/.env`; copies in `gen9-agent/models*.local.env` | The worker, the API, `make evals` | Put a new value in `.env` and `make up STACKS=models`: `ensure-keys.py` renames the old key `<alias>-retired-<UTC time>`, which keeps working, and creates the new one with the same scope and budget (its daily spend starts at 0). Rewrite the copy (`gen9-models/init-env.sh --from-env --force` with its file flag), restart gen9-agent, then delete the retired key: `POST /key/delete` with the master key and `{"key_aliases": ["<alias>-retired-…"]}`. **Run live:** all three, the old values refused after, a turn and a search by meaning on the new |
| `GEN9_SEARXNG_SECRET` | `gen9-models/.env` | SearXNG, the router's web search (`SEARXNG_SECRET`). Its limiter and image proxy are off, so it signs nothing Gen9 keeps | Replace it (`openssl rand -hex 32`) and `make up STACKS=models` |
| `LITELLM_SALT_KEY` | `gen9-models/.env` | LiteLLM encrypts what it stores with it | **Never.** A new one can't read what's stored; replacing it means starting the router over (`docker compose down -v`) |
| Provider keys (`OPENROUTER_API_KEY`, `OPENAI_API_KEY`, …) | `gen9-models/.env` | The router | Make a new key at the provider, put it in `.env`, restart gen9-models, revoke the old one at the provider |
| Langfuse project keys | `gen9-agent/langfuse.local.env` (and `LANGFUSE_INIT_PROJECT_*` in `gen9-langfuse/.env` for a first start) | The worker: traces, and erasing them | Create a new key pair in Langfuse (project settings), put it in `gen9-agent/langfuse.local.env`, restart gen9-agent, then delete the old pair in Langfuse. Without the UI, so the secret is never shown: set new `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` (`pk-lf-…`) and `LANGFUSE_INIT_PROJECT_SECRET_KEY` (`sk-lf-…`) in `gen9-langfuse/.env` and `make up STACKS=langfuse` (Langfuse's start creates a configured pair that doesn't exist yet), copy them into `gen9-agent/langfuse.local.env`, restart gen9-agent, and delete the old pair. A deleted pair works for up to a minute more (Langfuse caches keys for 60 s). **Run live** |
| `GEN9_AGENT_APP_DB_PASSWORD` | `gen9-postgres/.env`; `gen9-agent/postgres-app.local.env` | gen9-agent's API and worker, as `gen9_agent_app`: a role that owns nothing | Change it in `gen9-postgres/.env`, rewrite the copy (`gen9-postgres/init-env.sh --from-env --force --agent-app-env-file ../gen9-agent/postgres-app.local.env`), then `make up STACKS="postgres agent"`: gen9-postgres's `roles` job sets the new password on start |
| Database passwords (`GEN9_AGENT_DB_PASSWORD`, `KC_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, `GEN9_ADMIN_DB_PASSWORD`, each stack's `POSTGRES_PASSWORD`) | Each stack's `.env`; gen9-agent's copy in `postgres.local.env` (the owner's, read by its migrate job alone) | Each stack's services | `ALTER ROLE … PASSWORD …` as the database's owner, then the `.env` (and copy), then restart. `GEN9_ADMIN_DB_PASSWORD` is set again by the keys job on every start: changing `.env` is enough |
| `SANDBOX_API_KEY` | `gen9-sandbox/.env`; `gen9-agent/sandbox.local.env` | The worker, calling OpenSandbox; gen9-sandbox's disk watchdog (`launch.py`, as `OPENSANDBOX_SERVER_API_KEY`), removing an environment over its disk limit through the server's API | Change it in `gen9-sandbox/.env`, rewrite the copy (`init-env.sh --from-env --force`), restart gen9-sandbox and gen9-agent |
| Temporal's internode TLS (`tls.local.env`) | `gen9-temporal/tls.local.env` | Temporal's own services, to each other (a private CA) | `gen9-temporal/init-tls.sh --force` makes a new CA and certificate; restart gen9-temporal |
| Langfuse's own (`SALT`, `ENCRYPTION_KEY`, `NEXTAUTH_SECRET`; the passwords of its Postgres, ClickHouse, Redis (`REDIS_AUTH`) and MinIO (`MINIO_ROOT_PASSWORD`, also its `LANGFUSE_S3_*_SECRET_ACCESS_KEY`s); `LANGFUSE_INIT_USER_PASSWORD`, the first admin's) | `gen9-langfuse/.env` | Langfuse | As Langfuse's self-hosting guide says. `ENCRYPTION_KEY` encrypts stored secrets, so treat it like the router's salt key |
| A task's API trigger | Its hash, in `tasks.trigger_hash` | Whoever fires the task | *Make a new token* on the task (Scheduled, or `POST /v1/tasks/{id}/trigger`): the old one stops working at once |

Every rotation made through Gen9 (a trigger's token, connectors, environment secrets) is in the
audit record (`/admin/audit`).
