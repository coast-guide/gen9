# gen9-edge

Gen9 under one domain, over TLS, on Docker: Caddy in front of the other stacks, one host per
service people reach. Optional: without it, each stack serves on its own `127.0.0.1` port, as for
local development. On Kubernetes the cluster's Gateway does this job ([docs/operations.md,
"Kubernetes"](../docs/operations.md#kubernetes)).

## Quick start

```bash
make setup DOMAIN=gen9.example.com      # writes gen9-edge/.env; EDGE_TLS=you@example.com for Let's Encrypt
make up
```

| Host | Service |
| --- | --- |
| `gen9.example.com` | the web app (gen9-ui) |
| `id.gen9.example.com` | Keycloak: sign-in, the token issuer |
| `api.gen9.example.com` | gen9-agent's API: the terminal, MCP clients, Temporal's codec |
| `traces.gen9.example.com` | Langfuse |
| `traces-media.gen9.example.com` | Langfuse's media store |
| `temporal.gen9.example.com` | Temporal's web UI |
| `<id>.apps.gen9.example.com` | the connector apps' views, one host per connector |

Each host goes to the name its stack gives on its own network (`Caddyfile`); the edge joins only
those networks. The DNS names must point at the machine (a wildcard record, `*.gen9.example.com`,
covers them all), and ports 80 and 443 must reach it: `GEN9_EDGE_ADDRESS=0.0.0.0` in `.env` on a
server (the default, 127.0.0.1, serves only this machine).

## Files

| File | What |
| --- | --- |
| `compose.yaml` | The `edge` service: Caddy, pinned by digest; its settings |
| `Caddyfile` | One site per host, all under `GEN9_DOMAIN` |
| `.env` | `GEN9_DOMAIN`, and the other settings below; `make setup DOMAIN=…` writes it |
| `certs/` | A wildcard certificate of your own for `*.apps.` (not in git) |

## Settings

| Setting | Default | What |
| --- | --- | --- |
| `GEN9_DOMAIN` | `gen9.localhost` | The domain; `*.localhost` resolves to this machine without DNS |
| `GEN9_EDGE_TLS` | `internal` | `internal`: Caddy's own CA; an email: certificates from Let's Encrypt (ACME) |
| `GEN9_EDGE_APPS_TLS` | `internal` | The apps' wildcard host: Let's Encrypt issues a wildcard only by DNS challenge, so with ACME give one of your own, `"/certs/<cert> /certs/<key>"` |
| `GEN9_EDGE_ADDRESS` | `127.0.0.1` | Where ports 80 and 443 listen; `0.0.0.0` on a server |

## Caddy's own CA

With `GEN9_EDGE_TLS=internal`, browsers trust the certificates only once they trust Caddy's root:

```bash
docker compose cp edge:/data/caddy/pki/authorities/local/root.crt gen9-edge-root.crt
```

then add `gen9-edge-root.crt` to the machine's or the browser's trusted authorities. `make wipe
STACKS=edge` deletes the CA with the certificates: browsers then need the new root.
