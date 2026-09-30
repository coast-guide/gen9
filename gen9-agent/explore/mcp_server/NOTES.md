# Gen9 as an MCP server: FastMCP 4 in the agent API

Probe: `probe.py` (FastMCP 4.0.9, mcp 2.2.0), a FastAPI app with FastMCP's stateless HTTP app
mounted at `/`, trusting Gen9's Keycloak (`KeycloakAuthProvider`, realm `gen9`, 26.7.4). The
token came from the CLI's device flow (`token.mjs`: Puppeteer signs the seeded user in).

```
routes: ['/.well-known/oauth-protected-resource/mcp', '/mcp']
right audience ping: {'ok': True}                           FastAPI's own routes still answer
right audience no token: 401 Bearer scope="openid", resource_metadata="http://127.0.0.1:17999/.well-known/oauth-protected-resource/mcp"
right audience metadata: 200 {"resource":"http://127.0.0.1:17999/mcp","authorization_servers":["http://localhost:15000/realms/gen9"],"scopes_supported":["openid"],"bearer_methods_supported":["header"]}
right audience whoami: {'sub': 'cb89d9e7-…'}                a tool reads the token's person (get_access_token)
wrong audience refused: MCPError …                          "audience mismatch (got 'gen9-agent', expected 'someone-else')", 401
```

- The metadata's path inserts the MCP path after `/.well-known/oauth-protected-resource`
  (RFC 9728 §3.1), and `resource` is `base_url` + the MCP path.
- `KeycloakAuthProvider` requires the `openid` scope by default; `required_scopes` replaces it,
  and the challenge's `scope` lists them.
- The FastMCP app's `lifespan` must run (its session manager), also in stateless mode: the probe
  passed it as FastAPI's lifespan. Gen9's app has its own, so the two are nested.

## Client ID Metadata Documents in Keycloak 26.7.4

Probe: `cimd_probe.mjs`, on a throwaway `quay.io/keycloak/keycloak:26.7.4` (`start-dev
--features=cimd`, port 18999, realm `probe`), set up through the admin API:
- a `gen9-mcp` client scope with an Audience mapper (`http://localhost:17000/mcp`), a realm
  default *optional* scope, so a client that asks for it gets it;
- a client profile with the `client-id-metadata-document` executor (`cimd-allow-http-scheme`,
  `cimd-allow-permitted-domains`, `cimd-restrict-same-domain`, `only-allow-confidential-client`)
  and a policy with the `client-id-uri` condition (`client-id-uri-scheme`,
  `client-id-uri-allow-permitted-domains`). Names read from `/admin/serverinfo`.

With the feature on, discovery says `client_id_metadata_document_supported: true`. The client's
`client_id` was `http://host.docker.internal:<port>/client.json`, served by the probe:

```
first try: "Invalid Client ID: domain not allowed."   the executor checks every URI in the
                                                       document: the redirect's 127.0.0.1 too
with 127.0.0.1 and localhost trusted as well:
metadata fetched by Keycloak: 1 time(s)
consent page: Grant Access to CIMD probe … gen9-mcp … The client's hostname is host.docker.internal
callback: { code: (a code), iss: 'http://localhost:18999/realms/probe' }   RFC 9207's iss
token: { aud: 'http://localhost:17000/mcp', azp: 'http://host.docker.internal:61326/client.json',
         scope: 'openid email gen9-mcp profile' }
```

- Keycloak keeps each document's client as a client of the realm (two after two runs, one per
  URL): its issue #45284, "Persistent CIMD", is about that.
- The trusted domains must cover the redirect URIs' hosts as well as the document's.
