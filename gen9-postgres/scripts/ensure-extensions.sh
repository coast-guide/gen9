#!/bin/sh
# Every start: the extensions only the superuser may create, in each app database. Safe to repeat;
# a database created before an extension joined the image gets it here. Trusted extensions
# (pg_trgm) are left to the apps' own migrations.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname gen9_agent <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;
SQL
echo "gen9_agent: vector and pg_textsearch present"
