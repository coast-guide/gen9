#!/bin/sh
# Create or upgrade the schemas of Temporal's two databases. Safe on every start: setup-schema only
# records version 0.0 on an empty database, and update-schema applies only the versions newer than
# the one recorded, so after a server upgrade the next start brings the schema along (as Temporal's
# upgrade guide asks: schema first, then the new server).
# Adapted from github.com/temporalio/samples-server compose/scripts/setup-postgres.sh.
set -eu
: "${POSTGRES_SEEDS:?}" "${POSTGRES_USER:?}" "${SQL_PASSWORD:?}"
# TLS as the server has it (its SQL_CA and SQL_HOST_VERIFICATION, in temporal-sql-tool's own names):
# the certificate checked against the CA, its name too, or only encrypted
if [ "${SQL_TLS:-false}" = true ]; then
  [ -z "${SQL_CA:-}" ] || export SQL_TLS_CA_FILE="$SQL_CA"
  [ "${SQL_HOST_VERIFICATION:-false}" = true ] || export SQL_TLS_DISABLE_HOST_VERIFICATION=true
fi

for db in temporal:temporal temporal_visibility:visibility; do
  name=${db%%:*} dir=${db#*:}
  sql() { temporal-sql-tool --plugin postgres12 --ep "$POSTGRES_SEEDS" -p "${DB_PORT:-5432}" -u "$POSTGRES_USER" --db "$name" "$@"; }
  sql setup-schema -v 0.0
  sql update-schema -d "/etc/temporal/schema/postgresql/v12/$dir/versioned"
  echo "schema of $name is current"
done
