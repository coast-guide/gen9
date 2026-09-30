import "server-only";

import { z } from "zod";

// Compose passes a setting it wasn't given as "" (`${NAME:-}`): the same as unset
function unsetIfEmpty<T extends z.ZodType>(type: T) {
  return z.preprocess((value) => (value === "" ? undefined : value), type);
}

// Server-only settings, read at runtime (never NEXT_PUBLIC_*, so nothing reaches the browser bundle).
// Parsed lazily: `next build` runs without them.
const schema = z.object({
  /** Public origin of this app, as the browser sees it. Builds redirect_uri and post-logout URLs. */
  APP_URL: z.url(),
  /** Exact token issuer, e.g. http://localhost:15000/realms/gen9 */
  KEYCLOAK_ISSUER: z.url(),
  /** Where this server reaches Keycloak if not at the issuer's origin (containers: http://gen9-keycloak:8080) */
  KEYCLOAK_INTERNAL_URL: z.url().optional(),
  KEYCLOAK_CLIENT_ID: z.string().min(1),
  KEYCLOAK_CLIENT_SECRET: z.string().min(1),
  /** 32+ random bytes (hex). Encrypts session records at rest and names cookies' stored hashes */
  SESSION_SECRET: z.string().min(64),
  /** Valkey/Redis URL for server-side sessions */
  SESSION_STORE_URL: z.string().min(1),
  /** gen9-agent API base URL (as this server reaches it) */
  GEN9_AGENT_URL: z.url(),
  /** Where connectors' Views run (MCP Apps): an origin template, `{id}` naming each connector,
   * served by the sandbox service (sandbox/server.ts). Unset: Views aren't shown */
  MCP_APPS_SANDBOX_URL: z
    .string()
    .regex(/^https?:\/\/[a-z0-9.{}-]+(:\d+)?$/)
    .optional(),
  /** The privacy page (/privacy, GDPR Art. 13): who runs this Gen9 (name and address) and where to
   * write about personal data; optionally the data protection officer and the supervisory
   * authority. Unset: the page says the organization hasn't named itself yet */
  PRIVACY_CONTROLLER: unsetIfEmpty(z.string().optional()),
  PRIVACY_CONTACT: unsetIfEmpty(z.string().optional()),
  PRIVACY_DPO: unsetIfEmpty(z.string().optional()),
  PRIVACY_AUTHORITY: unsetIfEmpty(z.string().optional()),
  /** The organization's own privacy notice instead: /privacy redirects there */
  PRIVACY_NOTICE_URL: unsetIfEmpty(z.url().optional()),
});

export type Env = z.infer<typeof schema>;

let parsed: Env | undefined;

export function env(): Env {
  parsed ??= schema.parse(process.env);
  return parsed;
}

/** What is wrong with the settings, one line each, or nothing (instrumentation.ts checks at start). */
export function envProblems(settings: Record<string, string | undefined> = process.env): string[] {
  const result = schema.safeParse(settings);
  return result.success ? [] : result.error.issues.map((issue) => `${issue.path.join(".") || "settings"}: ${issue.message}`);
}

/** Secure cookies need https; plain http is allowed only for local development on localhost. */
export function isSecureOrigin(): boolean {
  return new URL(env().APP_URL).protocol === "https:";
}
