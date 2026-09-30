#!/bin/sh
# Every start: the login role gen9-agent's services (its API and worker) connect as,
# gen9_agent_app, with its password from GEN9_AGENT_APP_DB_PASSWORD, so a new password in .env
# applies on the next start. It owns nothing and may create nothing: gen9-agent's migrations, run
# as the owner gen9_agent, grant it what it may do on the tables. The owner stays the migrate
# job's alone (docs/plans/harness.md, "The services stop owning the tables").
set -eu
: "${GEN9_AGENT_APP_DB_PASSWORD:?run make setup: it adds GEN9_AGENT_APP_DB_PASSWORD to gen9-postgres/.env}"
# \getenv reads the password inside psql: it never appears on a command line
psql -v ON_ERROR_STOP=1 --quiet --username "$POSTGRES_USER" --dbname gen9_agent <<'SQL'
\getenv pw GEN9_AGENT_APP_DB_PASSWORD
SELECT NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gen9_agent_app') AS missing \gset
\if :missing
CREATE ROLE gen9_agent_app;
\endif
ALTER ROLE gen9_agent_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'pw';
GRANT CONNECT ON DATABASE gen9_agent TO gen9_agent_app;
SQL
echo "gen9_agent: role gen9_agent_app ready"
