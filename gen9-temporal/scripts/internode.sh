#!/bin/sh
# Runs a command as one of this stack's own tools: the temporal CLI on the internal frontend
# (temporal:7236), with the internode certificate from tls.local.env. That file holds base64 PEM
# under the server's variable names; the CLI takes inline PEM under its own names.
#   docker compose run --rm cli temporal workflow list
set -eu
: "${TEMPORAL_TLS_SERVER_CA_CERT_DATA:?needs tls.local.env (./init-tls.sh)}"
decode() { printf %s "$1" | base64 -d; }
TEMPORAL_TLS_CLIENT_CERT_DATA=$(decode "$TEMPORAL_TLS_SERVER_CERT_DATA")
TEMPORAL_TLS_CLIENT_KEY_DATA=$(decode "$TEMPORAL_TLS_SERVER_KEY_DATA")
TEMPORAL_TLS_SERVER_CA_CERT_DATA=$(decode "$TEMPORAL_TLS_SERVER_CA_CERT_DATA")
TEMPORAL_TLS_SERVER_NAME=$TEMPORAL_TLS_INTERNODE_SERVER_NAME
export TEMPORAL_TLS_CLIENT_CERT_DATA TEMPORAL_TLS_CLIENT_KEY_DATA TEMPORAL_TLS_SERVER_CA_CERT_DATA TEMPORAL_TLS_SERVER_NAME
unset TEMPORAL_TLS_SERVER_CERT_DATA TEMPORAL_TLS_SERVER_KEY_DATA TEMPORAL_TLS_INTERNODE_SERVER_NAME
exec "$@"
