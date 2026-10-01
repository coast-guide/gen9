#!/usr/bin/env bash
# Asks the probe's Loki, over TLS with the probe's certificate authority: ./query.sh labels
# container | ./query.sh '{container="gen9-agent-api-1"} |= "audit {"' [since, default 1h]
set -euo pipefail
cd "$(dirname "$0")"
loki() { curl -sS --cacert certs/ca.pem "https://localhost:19310/loki/api/v1/$1" "${@:2}"; }
if [ "$1" = labels ]; then
  loki "label/$2/values" | python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)["data"]))'
else
  loki query_range -G --data-urlencode "query=$1" --data-urlencode "since=${2:-1h}" \
    --data-urlencode limit=5000 |
    python3 -c 'import json, sys
for s in json.load(sys.stdin)["data"]["result"]:
    for _, line in s["values"]:
        print(s["stream"].get("container"), line)'
fi
