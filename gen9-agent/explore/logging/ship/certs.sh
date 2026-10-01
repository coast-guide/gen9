#!/usr/bin/env bash
# A throwaway certificate authority and Loki's certificate (for the name `loki`), in ./certs
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p certs && cd certs
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=gen9 probe CA" \
  -keyout ca-key.pem -out ca.pem 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=loki" -keyout loki-key.pem -out loki.csr 2>/dev/null
openssl x509 -req -in loki.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 2 \
  -extfile <(printf 'subjectAltName=DNS:loki,DNS:localhost') -out loki.pem 2>/dev/null
# Loki runs as its own user (10001): it must read its key
chmod 644 loki-key.pem
rm -f loki.csr ca.srl
echo "certs: ca.pem, loki.pem"
