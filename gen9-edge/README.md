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

`make setup DOMAIN=…` also writes every address browsers and terminals use into the stacks'
settings files, one host per service as above: Keycloak's issuer (`KC_HOSTNAME`, and the
`*.local.env` that carry it), the addresses Keycloak lets sign-ins return to, `GEN9_UI_URL`,
gen9-agent's `GEN9_API_PUBLIC_URL`, Langfuse's `NEXTAUTH_URL` and media, Temporal's UI and its
codec, the apps' `MCP_APPS_SANDBOX_URL`; and `gen9-keycloak/edge.local.env`, so Keycloak reads the
edge's forwarded headers. Containers keep calling each other inside. `make setup DOMAIN=localhost`
puts every address back on this machine's ports, and leaves gen9-edge unset (`make down
STACKS=edge` stops it).

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
| `certs/` | A wildcard certificate of your own for `*.apps.`, if you give one (not in git) |

## Settings

| Setting | Default | What |
| --- | --- | --- |
| `GEN9_DOMAIN` | `gen9.localhost` | The domain; `*.localhost` resolves to this machine without DNS |
| `GEN9_EDGE_TLS` | `internal` | `internal`: Caddy's own CA; an email: certificates from Let's Encrypt (ACME) |
| `GEN9_EDGE_APPS` | `on-demand` | The connector apps' hosts, one per connector (`<id>.apps.…`): `on-demand`, each host's certificate at its first handshake, from `GEN9_EDGE_TLS`'s issuer, once gen9-agent says the connector exists (below); `own`, a wildcard of your own |
| `GEN9_EDGE_APPS_TLS` | | With `GEN9_EDGE_APPS=own`: the wildcard's files, `"/certs/<cert> /certs/<key>"` |
| `GEN9_EDGE_ADDRESS` | `127.0.0.1` | Where ports 80 and 443 listen; `0.0.0.0` on a server |

## The connector apps' hosts

Each connector's Views have a host of their own, so none shares another's origin. Caddy gets each
host's certificate on demand, at the host's first handshake (a few seconds, once), and only after
asking gen9-agent whether that connector exists (`on_demand_tls`'s `ask`,
`http://gen9-agent:8000/internal/apps-host`, which this edge doesn't serve from outside): a
made-up name's handshake fails, and costs nothing. With Let's Encrypt each host's certificate
counts against its limits for the domain, which many connectors could use up; a wildcard of your
own avoids that (`GEN9_EDGE_APPS=own`; Let's Encrypt issues wildcards only by DNS challenge).

## Caddy's own CA

With `GEN9_EDGE_TLS=internal`, browsers trust the certificates only once they trust Caddy's root:

```bash
docker compose cp edge:/data/caddy/pki/authorities/local/root.crt gen9-edge-root.crt
```

then add `gen9-edge-root.crt` to the machine's or the browser's trusted authorities. `make wipe
STACKS=edge` deletes the CA with the certificates: browsers then need the new root.
