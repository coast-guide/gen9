# Cryptography in Gen9

What Gen9 encrypts, signs, hashes and generates: the algorithm, the key, where the key is made and
kept, and what uses it. This is the cryptographic inventory OWASP ASVS 5.0 asks for (V11.1). How to
replace each secret is in [secrets.md](secrets.md). Read from the code and the running stacks
(docs/plans/manual-e2e.md, P7-C1).

## Encryption

| What it protects | Algorithm | Key | Made by, kept in | Used by |
| --- | --- | --- | --- | --- |
| People's connector tokens, connector sign-ins and environment secrets, in Postgres | AES-256-GCM, a random 96-bit nonce each; bound to its owner and connector as associated data, so it doesn't open on another row | `GEN9_SECRET_KEYS`, 256-bit keys in a ring (the first seals, all open) | `make setup` (`openssl rand -base64 32`), `gen9-agent/.env` | gen9-agent's API and worker (`vault.py`) |
| What gen9-agent sends through Temporal: workflow and Activity inputs, results, failures | AES-256-GCM, a random 96-bit nonce each | `TEMPORAL_PAYLOAD_KEYS`, 256-bit keys in a ring | `make setup`, `gen9-agent/.env` | gen9-agent's API and worker, and Temporal UI's codec endpoint (`codec.py`) |
| Web sessions in Valkey, refresh tokens among them | AES-256-GCM, a random 96-bit IV each; the key derived with HKDF-SHA256 | `SESSION_SECRET`, 256 bits | `gen9-ui/init-env.sh`, `gen9-ui/.env` | gen9-ui (`lib/auth/crypto.ts`) |
| Temporal's services, to each other | TLS with a private CA: EC P-256 keys, SHA-256 signatures; the CA for 10 years, the certificate for 825 days | Generated | `gen9-temporal/init-tls.sh`, `gen9-temporal/tls.local.env` | Temporal's frontend, history, matching and worker services |
| Langfuse's stored secrets (its LLM connections) | Langfuse's own | `ENCRYPTION_KEY`, 256 bits | `gen9-langfuse/init-env.sh`, `gen9-langfuse/.env` | Langfuse |
| LiteLLM's stored credentials | LiteLLM's own | `LITELLM_SALT_KEY` | `gen9-models/init-env.sh`, `gen9-models/.env` | LiteLLM |

## Signatures

| What | Algorithm | Key | Kept in | Checked by |
| --- | --- | --- | --- | --- |
| Access, ID and logout tokens | RS256 (RSA 2048, SHA-256) | The realm's `rsa-generated` key; rotated as secrets.md says | Keycloak's database | gen9-agent (RS256 only, `auth.py`); gen9-ui (`openid-client`; logout tokens RS256 only, typed `logout+jwt`); Temporal (its JWT authorizer) |
| Refresh tokens and Keycloak's own tokens | HS512 | The realm's `hmac-generated-hs512` key | Keycloak's database | Keycloak only |

Keycloak also keeps an RSA-OAEP and an AES key of its own; none of Gen9's clients asks for encrypted
tokens.

## Stored as hashes

| What | How | Where |
| --- | --- | --- |
| People's passwords | Argon2id, Keycloak's default: 5 passes, 7 MiB, 1 lane, a 32-byte hash. It's one of the settings in OWASP's Password Storage Cheat Sheet | Keycloak's database (`credential`) |
| Authenticator apps | TOTP (RFC 6238): HMAC-SHA1, 6 digits, 30 seconds. The secret itself is kept, as TOTP needs | Keycloak's database |
| Database passwords | SCRAM-SHA-256 (each Postgres's `password_encryption`). Without a password only over the container's own Unix socket | Each stack's Postgres |
| A task's trigger token | 256 random bits; SHA-256, compared in constant time | gen9-agent's database (`tasks.trigger_hash`) |
| A connector sign-in's `state` | 256 random bits; SHA-256 | gen9-agent's database |
| Web session ids | 256 random bits; stored under their SHA-256 | Valkey |
| The router's keys | 192 random bits; SHA-256 (LiteLLM's) | gen9-models' database |

## Random values

Every value meant to be unguessable comes from the operating system's generator: `os.urandom`
and `secrets` in Python, `crypto.randomBytes` in Node, `openssl rand` (else `/dev/urandom`) in the
setup scripts. Keys and tokens are 256 bits, passwords between services at least 128. The web
app's CSP nonce is `crypto.randomUUID()`, 122 random bits, as Next.js's guide does it (P7-C2).

SHA-256 also names things, with no secret involved: an agent folder's version, a search index, a
file's or a plugin's content.

## Toward post-quantum cryptography

- **Encryption and hashes:** AES-256 and SHA-256 keep 128-bit security against a quantum computer,
  so they stay.
- **Key exchange:** Gen9's own TLS clients already use the hybrid X25519MLKEM768 where a server
  offers it. That's OpenSSL 3.5's default, in gen9-agent's image (3.5.7, which Python uses) and in
  Node 24's (3.5.8): both negotiated it with a server that offers it.
- **Signatures:** RS256 and P-256 aren't post-quantum. When Keycloak can sign with a
  post-quantum algorithm (ML-DSA), the change goes as a key rotation (secrets.md): its algorithm
  added to gen9-agent's and gen9-ui's allowlists first, the key published passive, then made
  active. Temporal's private CA stays on P-256 until Temporal's TLS takes post-quantum
  certificates.
