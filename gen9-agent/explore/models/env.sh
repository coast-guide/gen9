#!/usr/bin/env bash
# Writes this probe's .env: generated secrets plus only the keys it needs from gen9-agent's files.
set -euo pipefail
cd "$(dirname "$0")"
val() { sed -n "s/^$1=//p" "$2" | tail -n 1; }
umask 077
{
  echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
  echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)"
  echo "OPENAI_API_KEY=$(val OPENAI_API_KEY ../../.env)"
  echo "LANGFUSE_PUBLIC_KEY=$(val LANGFUSE_PUBLIC_KEY ../../langfuse.local.env)"
  echo "LANGFUSE_SECRET_KEY=$(val LANGFUSE_SECRET_KEY ../../langfuse.local.env)"
} > .env
echo "wrote .env: $(cut -d= -f1 .env | tr '\n' ' ')"
