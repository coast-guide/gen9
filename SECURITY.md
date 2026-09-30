# Security policy

## Report a vulnerability

Report it privately, not in a public issue:
[report a vulnerability](https://github.com/coast-guide/gen9/security/advisories/new) (GitHub's
private vulnerability reporting). Say what you found, how to reproduce it, the commit or version,
and a fix or mitigation if you know one.

What happens next: the maintainer acknowledges the report within 7 days. A confirmed issue gets a
GitHub Security Advisory and a fix, and is disclosed once the fix is out, or 90 days after the
report, whichever comes first. You're credited in the advisory unless you'd rather not be.

## Supported versions

Gen9 has no releases yet: fixes go to `main`.

## Scope

In scope: the code in this repository and the configuration it ships (the Compose files, the
settings `make setup` generates, the Keycloak realm and theme, the model router's config).

Out of scope: vulnerabilities in the software Gen9 runs, such as Keycloak, Temporal, Langfuse,
LiteLLM, PostgreSQL and OpenSandbox. Report those to their projects, and tell us too if Gen9's
configuration makes one easier to exploit.

## How Gen9 is secured

- Identity, tokens and who may call what: [docs/auth-architecture.md](docs/auth-architecture.md)
- Every secret, where it lives and how to replace it: [docs/secrets.md](docs/secrets.md)
- Where the agent runs commands, and what it can reach: [gen9-sandbox/README.md](gen9-sandbox/README.md)
- The model router's hardening: [gen9-models/README.md](gen9-models/README.md)
- Stopping every agent at once: [docs/operations.md, "Stop every agent at once"](docs/operations.md#stop-every-agent-at-once)
