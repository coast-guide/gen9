#!/usr/bin/env bash
# First start only (empty data directory): one database per app, each owned by its own
# login role without superuser rights. pgvector is not a trusted extension (its vector.control
# has no `trusted = true`), so the superuser creates it here; apps only use it.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v pw="$GEN9_AGENT_DB_PASSWORD" <<'SQL'
CREATE ROLE gen9_agent LOGIN PASSWORD :'pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE gen9_agent OWNER gen9_agent;
REVOKE ALL ON DATABASE gen9_agent FROM PUBLIC;
-- App roles connect only to their own database
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname gen9_agent <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL
