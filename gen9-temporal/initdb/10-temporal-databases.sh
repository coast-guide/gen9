#!/usr/bin/env bash
# First start only (empty data directory): the role Temporal connects as, without superuser rights,
# and its two databases: `temporal` (workflow state) and `temporal_visibility` (the index behind
# listing and searching workflows). The schema job fills them with temporal-sql-tool.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v pw="$TEMPORAL_DB_PASSWORD" <<'SQL'
CREATE ROLE temporal LOGIN PASSWORD :'pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
REVOKE ALL ON DATABASE temporal, temporal_visibility FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
SQL

# Temporal's visibility schema (v1.2) needs btree_gin; created here so the schema job never needs
# more than the database owner's rights
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname temporal_visibility <<'SQL'
CREATE EXTENSION IF NOT EXISTS btree_gin;
SQL
