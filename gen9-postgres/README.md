# gen9-postgres

Postgres 18 with [pgvector](https://github.com/pgvector/pgvector) 0.8.6 (vector search) and [pg_textsearch](https://github.com/timescale/pg_textsearch) 1.4.0 (BM25 full-text search, PostgreSQL licence): the application database for Gen9 services. It runs as an independent stack, and apps only need a connection URL.

Keycloak and Langfuse keep their own databases in their own stacks. This one holds only application data: `gen9-agent` users, threads, agent checkpoints and embeddings.

**Requires:** Docker with Compose v2, bash.

## Quick start

```bash
./init-env.sh --agent-env-file ../gen9-agent/postgres.local.env \
  --agent-app-env-file ../gen9-agent/postgres-app.local.env        # generate .env (random passwords)
for n in gen9-postgres; do docker network inspect $n >/dev/null 2>&1 || docker network create $n; done   # once; make up does it
docker compose up -d --wait       # start, wait until healthy
```

Connection URLs for `gen9-agent`: its services connect as a role that owns nothing, and only its
migrate job as the owner:

```
postgresql://gen9_agent_app:<GEN9_AGENT_APP_DB_PASSWORD from .env>@localhost:16000/gen9_agent   # API, worker
postgresql://gen9_agent:<GEN9_AGENT_DB_PASSWORD from .env>@localhost:16000/gen9_agent           # migrations
```

From another stack's container, join the `gen9-postgres` network and use `gen9-postgres:5432` (as gen9-agent does). `make up` creates the network; by hand: `docker network create gen9-postgres`.

> [!IMPORTANT]
> `GEN9_AGENT_DB_PASSWORD` is read only when the data volume is first created, and `init-env.sh`
> refuses to regenerate passwords once the volume exists. `GEN9_AGENT_APP_DB_PASSWORD` is set again
> on every start, so changing it in `.env` (and `gen9-agent/postgres-app.local.env`) is enough.

## What gets created (first start only)

`initdb/10-app-databases.sh` runs once, on an empty data directory:

| Object | Details |
| --- | --- |
| Role `gen9_agent` | Login, no superuser, no `CREATEDB`/`CREATEROLE`. SCRAM-SHA-256 password. Owns the database and every table: only gen9-agent's migrate job connects as it |
| Database `gen9_agent` | Owned by `gen9_agent`; `CONNECT` and `TEMP` revoked from `PUBLIC` |
| Extension `vector` | Created by the superuser: pgvector is not a *trusted* extension (its `vector.control` has no `trusted = true`), so the app role could not create it |
| Extension `pg_textsearch` | Created by the superuser too (not trusted either), and kept on every start (below) |
| `postgres` database | `CONNECT` revoked from `PUBLIC`, so app roles reach only their own database |

Cluster settings:
- `initdb --data-checksums --auth-host=scram-sha-256`
- `maintenance_work_mem=512MB`, with `shm_size: 1g`. pgvector says `--shm-size` must be at least `maintenance_work_mem`, or parallel HNSW index builds fail.
- Statements slower than 500 ms are logged.
- `shared_preload_libraries=pg_textsearch`: without it, `CREATE EXTENSION pg_textsearch` fails
  ("library not loaded").

**On every start**, two jobs do what only the superuser may, both safe to repeat. `ready` holds
`docker compose up --wait` until they have run:
- `extensions` creates `vector` and `pg_textsearch` in each app database
  (`scripts/ensure-extensions.sh`), so a database made before an extension joined the image gets
  it too;
- `roles` creates `gen9_agent_app`, the role gen9-agent's API and worker connect as, and sets its
  password from `GEN9_AGENT_APP_DB_PASSWORD` (`scripts/ensure-roles.sh`). It may log in and
  `CONNECT`, and nothing else here: no `CREATEDB`, `CREATEROLE`, `TEMP`, or `CREATE` on a
  schema. gen9-agent's migrations, as the owner, grant it rows (gen9-agent's `migrate.py`).
  OWASP's Database Security Cheat Sheet: the application's account "should not be the owner of
  the database". Trusted extensions such as `pg_trgm` are left to the
apps' own migrations.

Search notes (gen9-agent/explore/search/NOTES.md):
- With bound parameters, name the BM25 index: `col <@> to_bm25query($1, 'index')`.
- `<@>` scores rows that don't match 0; filter with `< 0` or `@@ websearch_to_tsquery(...)`.
- A BM25 index build logs NOTICEs (documents, average length).

To add a database for another app, copy the block in `initdb/10-app-databases.sh`. On an existing volume, run the same SQL once with `docker compose exec postgres psql -U postgres`.

## Files

| File | Purpose |
| --- | --- |
| `compose.yaml` | `postgres` (built here), the `extensions` and `roles` jobs, `ready` |
| `Dockerfile` | Postgres's official image, pinned by digest, with pgvector from apt.postgresql.org (version pinned) and pg_textsearch's release package for the build's architecture, pinned by SHA-256 |
| `scripts/ensure-extensions.sh` | The superuser's extensions, on every start |
| `scripts/ensure-roles.sh` | gen9-agent's services' role and its password, on every start |
| `initdb/` | First-start SQL: app roles, databases, extensions |
| `init-env.sh` | Generates `.env` (superuser + app roles' passwords) and gen9-agent's two settings files. See `--help` |
| `.env` | Generated, secret, gitignored |

## Ports

| Port | Service | `.env` variable |
| --- | --- | --- |
| `16000` | Postgres | `GEN9_POSTGRES_PORT` |

## Verify

```bash
docker compose exec postgres psql -U postgres -d gen9_agent -c "select extversion from pg_extension where extname='vector'"   # 0.8.6
docker compose exec -e PGPASSWORD=<app pw> postgres psql -h 127.0.0.1 -U gen9_agent -d gen9_agent \
  -c "select '[1,2]'::vector <-> '[2,3]'::vector"          # 1.414…
docker compose exec -e PGPASSWORD=<app pw> postgres psql -h 127.0.0.1 -U gen9_agent -d gen9_agent \
  -c "create extension pg_stat_statements"                 # ERROR: must be superuser (as intended)
```

## Operate

| Task | Command |
| --- | --- |
| Status | `docker compose ps` |
| psql (superuser) | `docker compose exec postgres psql -U postgres -d gen9_agent` |
| Backup | `make backup DIR=…` at the root backs up every stack, keys included, and `make restore DIR=…` restores it ([docs/operations.md, "Back up and restore"](../docs/operations.md#back-up-and-restore)). A logical dump of this database alone, for another major version of Postgres: `docker compose exec postgres pg_dump -U postgres -Fc gen9_agent > gen9_agent.dump` |
| Stop (keeps data) | `docker compose down` |
| Reset (**deletes all data**) | `docker compose down -v`, then `./init-env.sh --force` with the two `--agent-…-env-file` flags |

## Upgrade

- **Minor update, or a rebuild with Debian's fixes** (`make updates` lists it): re-pin the base image digest in the `Dockerfile` (`docker buildx imagetools inspect postgres:<ver>-trixie`), then `docker compose up -d --build --wait`.
- **pgvector:** set `PGVECTOR_VERSION` in the `Dockerfile` to a version of `postgresql-18-pgvector` that apt.postgresql.org has (`apt-cache policy postgresql-18-pgvector` in the image), rebuild, then `ALTER EXTENSION vector UPDATE;` as the superuser.
- **pg_textsearch:** bump its version and both SHA-256 values in the `Dockerfile` (`shasum -a 256` on the release's `pg18-amd64` and `pg18-arm64` zips), rebuild, then `ALTER EXTENSION pg_textsearch UPDATE;`.
- **Major (18 → 19):** the volume is mounted at `/var/lib/postgresql`, the parent of the Postgres 18+ image's versioned `PGDATA`, so `pg_upgrade --link` can run across versions.
