import "server-only";

import * as client from "openid-client";

import { env } from "@/lib/env";

/**
 * OpenID Connect client for Keycloak (openid-client: OpenID Certified relying party).
 *
 * Metadata is fetched from the back-channel URL (KEYCLOAK_INTERNAL_URL in containers). Keycloak runs
 * with a fixed frontend hostname and a dynamic back-channel, so that document advertises
 * `issuer` and `authorization_endpoint` on the public origin (what browsers use) and token, JWKS,
 * userinfo on the internal origin (what this server uses). We check `issuer` ourselves, since
 * discovery() would require it to equal the URL we fetched from.
 */
let configuration: Promise<client.Configuration> | undefined;

async function discover(): Promise<client.Configuration> {
  const { KEYCLOAK_ISSUER, KEYCLOAK_INTERNAL_URL, KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET } = env();
  const issuer = new URL(KEYCLOAK_ISSUER);
  const base = KEYCLOAK_INTERNAL_URL ? new URL(issuer.pathname, KEYCLOAK_INTERNAL_URL) : issuer;
  const response = await fetch(`${base.href.replace(/\/$/, "")}/.well-known/openid-configuration`, {
    cache: "no-store",
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`OIDC discovery failed: HTTP ${response.status}`);
  const metadata = (await response.json()) as client.ServerMetadata;
  if (metadata.issuer !== KEYCLOAK_ISSUER) {
    throw new Error(`OIDC discovery: issuer ${metadata.issuer} does not match KEYCLOAK_ISSUER ${KEYCLOAK_ISSUER}`);
  }
  const config = new client.Configuration(
    metadata,
    KEYCLOAK_CLIENT_ID,
    undefined,
    client.ClientSecretBasic(KEYCLOAK_CLIENT_SECRET),
  );
  // Plain HTTP to Keycloak is only acceptable because both sides are local (127.0.0.1-bound)
  if (base.protocol === "http:") client.allowInsecureRequests(config);
  return config;
}

export function oidc(): Promise<client.Configuration> {
  configuration ??= discover().catch((error) => {
    configuration = undefined; // retry on the next request
    throw error;
  });
  return configuration;
}

/**
 * Whether Keycloak answers now. Sign-in sends the browser there, and while it is down the browser would show
 * its own "refused to connect" instead of Gen9's words (found by hand: docs/plans/manual-e2e.md, P2-J5).
 * Asked on the back-channel, as this server reaches it; a fresh request, since the metadata above is cached.
 */
export async function keycloakAnswers(): Promise<boolean> {
  const { KEYCLOAK_ISSUER, KEYCLOAK_INTERNAL_URL } = env();
  const issuer = new URL(KEYCLOAK_ISSUER);
  const base = KEYCLOAK_INTERNAL_URL ? new URL(issuer.pathname, KEYCLOAK_INTERNAL_URL) : issuer;
  try {
    const response = await fetch(`${base.href.replace(/\/$/, "")}/.well-known/openid-configuration`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export function redirectUri(): string {
  return new URL("/auth/callback", env().APP_URL).href;
}

export { client };
