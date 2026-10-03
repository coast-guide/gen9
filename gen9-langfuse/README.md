# gen9-langfuse

Self-hosted [Langfuse](https://langfuse.com) v4 for tracing, prompts and evals. Runs as an independent stack: apps only need its URL and two keys.

**Requires:** Docker with Compose v2 (tested with v5.5.1), bash.

## Quick start

```bash
./init-env.sh --email you@example.com --name "Your Name"   # generate .env
for n in gen9-langfuse; do docker network inspect $n >/dev/null 2>&1 || docker network create $n; done   # once; make up does it
docker compose up -d                                         # start (ready in ~2–3 min)
curl -fsS http://localhost:13000/api/public/ready && echo    # check
grep ^LANGFUSE_INIT_USER_PASSWORD= .env                      # login password
```

Log in at http://localhost:13000 → org **Gen9** → project **gen9-agent**.

> [!IMPORTANT]
> Never regenerate `.env` once data exists: new secrets won't match the database (the script refuses).

## Files

| File                      | Purpose                                                                    |
| ------------------------- | -------------------------------------------------------------------------- |
| `docker-compose.yml`    | Upstream Langfuse file, pinned to a release.**Never edit.**          |
| `compose.override.yaml` | Our changes: image pins (`tag@digest`), ports, media URLs, ClickHouse's config, and `migrated`, which holds the worker back until the web has migrated, so it loads the newest model prices at start |
| `clickhouse/disk.xml`   | ClickHouse's own logs, bounded ([Disk](#disk))                             |
| `init-env.sh`           | Generates`.env` (secrets, URLs, ports, org/project/keys). See `--help` |
| `.env`                  | Generated, secret, gitignored                                              |

## Ports

All on `127.0.0.1`, in an uncommon block to avoid clashes. To change one, edit its variable in `.env`, then `docker compose up -d`.

| Port      | Service                | `.env` variable                   |
| --------- | ---------------------- | ----------------------------------- |
| `13000` | UI + API               | `LANGFUSE_PORT`                   |
| `13001` | Media storage (S3 API) | `LANGFUSE_MEDIA_PORT`             |
| `13002` | MinIO console          | `LANGFUSE_MINIO_CONSOLE_PORT`     |
| `13003` | Worker health          | `LANGFUSE_WORKER_PORT`            |
| `13004` | Postgres               | `LANGFUSE_POSTGRES_PORT`          |
| `13005` | Redis                  | `LANGFUSE_REDIS_PORT`             |
| `13006` | ClickHouse HTTP        | `LANGFUSE_CLICKHOUSE_HTTP_PORT`   |
| `13007` | ClickHouse native      | `LANGFUSE_CLICKHOUSE_NATIVE_PORT` |

## Connect an app

```bash
LANGFUSE_BASE_URL=http://localhost:13000
LANGFUSE_PUBLIC_KEY=   # LANGFUSE_INIT_PROJECT_PUBLIC_KEY from .env
LANGFUSE_SECRET_KEY=   # LANGFUSE_INIT_PROJECT_SECRET_KEY from .env
```

## Operate

| Task                               | Command                                                        |
| ---------------------------------- | -------------------------------------------------------------- |
| Status                             | `docker compose ps`                                          |
| Logs                               | `docker compose logs --tail=50 langfuse-web langfuse-worker` |
| Stop (keeps data)                  | `docker compose down`                                        |
| Reset (**deletes all data**) | `docker compose down -v`, then `./init-env.sh ... --force` |

## Disk

Traces grow with every run; Gen9 erases a chat's or an account's traces when they're deleted
(Langfuse's time-based data retention is its Enterprise Edition's when self-hosted). Langfuse also
keeps a raw copy of every batch it ingests in MinIO (`events/`, the messages in them), filed by
time, which that erasure doesn't reach: the one-shot `minio-lifecycle` job gives them a one-day
expiry rule on every start (Langfuse's blob storage guide recommends one; it re-reads them only to
retry ingestion), so a deleted chat's text leaves them within two days (S3 rounds an expiry to the
next midnight UTC). `docker compose run --rm --no-deps --entrypoint sh minio-lifecycle -c 'mc alias
set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc ilm rule ls
local/langfuse'` shows the rule. ClickHouse's
own logs are bounded by `clickhouse/disk.xml`, mounted by `compose.override.yaml`: its system log
tables that have no TTL (`trace_log`, `text_log`, `metric_log`, …) are off, as Langfuse's scaling
guide advises (Langfuse reads none of them), and its text log keeps warnings in at most three
files of 100 MB (the image writes everything, in up to ten files of 1 GB). After a day's use,
dropping those tables took ClickHouse's data from 178 MB to 72 MB.

On an install from before that change, the old tables stay until dropped (ClickHouse's own logs,
no Gen9 data):

    for t in trace_log text_log opentelemetry_span_log asynchronous_metric_log metric_log latency_log; do
      docker exec gen9-langfuse-clickhouse-1 clickhouse-client -q "DROP TABLE IF EXISTS system.$t SYNC"
    done

## Images in traces

A trace's images live in MinIO, not in the trace: the SDK asks Langfuse for a presigned URL on the
media URL (`LANGFUSE_MEDIA_PUBLIC_URL`, `http://localhost:13001` here) and uploads there, and the
browser loads them from the same URL. Langfuse needs that address reachable by both
([Blob storage](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage)), and in
gen9-agent's container localhost is the container itself. So MinIO also joins the `gen9-langfuse`
network as `gen9-langfuse-media`, and gen9-agent's worker sends uploads for a loopback address
there, keeping the Host header the URL's signature covers (`gen9-agent/src/gen9_agent/langfuse_tracer.py`).
A public media URL (below) is reached as it is.

## Deploy on a VM

Put a TLS reverse proxy in front of `127.0.0.1:13000` (UI/API) and `127.0.0.1:13001` (media), and generate `.env` with its public URLs:

```bash
./init-env.sh --email you@example.com --name "Your Name" \
  --url https://langfuse.example.com --media-url https://media.langfuse.example.com
```

Its Postgres, Redis, ClickHouse and S3 can each be elsewhere instead (a managed service, a
cluster's operator): Langfuse's own settings in `.env`, the store left out of `COMPOSE_PROFILES`
([docs/operations.md, "External services"](../docs/operations.md#external-services)).

## Upgrade

1. Read the release's upgrade notes, then replace the upstream file:
   `curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/langfuse/langfuse/vX.Y.Z/docker-compose.yml`
2. Re-pin changed images in `compose.override.yaml` (digest from `docker buildx imagetools inspect <image>:<tag>`).
3. `docker compose pull && docker compose up -d`

### ClickHouse's version

Upstream's file pins ClickHouse 25.12, which ClickHouse stopped fixing after 25.12.11.4 (30 April
2026; its `SECURITY.md` lists the lines it supports). Gen9 runs its LTS line instead, 26.8, which
Langfuse v4 supports (at least 25.12, “26.4 recommended”, in Langfuse's ClickHouse guide) and has
the fixes for: the 26.x query that broke the Scores page (langfuse#15125, worked around in v4 by
#16019) and timestamps misread on 26.8 (langfuse#16858, fixed by #16892). Moving up a line is one
step within a year (ClickHouse's upgrade guide); back up first (`make backup STACKS=langfuse`),
since a newer line's data may not open on an older one. When upgrading Langfuse, keep a line that
both ClickHouse supports and Langfuse runs on.

### MinIO's source

MinIO's own repository is archived (its last release RELEASE.2025-10-15). The image upstream's
file names, `cgr.dev/chainguard/minio`, is Chainguard's build of its fork
([chainguard-forks/minio](https://github.com/chainguard-forks/minio)), which still ships fixes
as releases (RELEASE.2026-09-22 refuses unsigned `x-amz-*` headers in Signature V4). Its free
tier publishes only `latest`, so the pin is a digest: `docker run --rm --entrypoint minio <image>
--version` names the release a digest holds.
