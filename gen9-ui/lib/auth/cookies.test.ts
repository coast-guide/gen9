import { afterEach, describe, expect, it, vi } from "vitest";

// Settings as make setup writes them, with the app's origin as each case wants
function withOrigin(appUrl: string) {
  vi.resetModules();
  vi.stubEnv("APP_URL", appUrl);
  vi.stubEnv("KEYCLOAK_ISSUER", "http://localhost:15000/realms/gen9");
  vi.stubEnv("KEYCLOAK_CLIENT_ID", "gen9-ui");
  vi.stubEnv("KEYCLOAK_CLIENT_SECRET", "x");
  vi.stubEnv("SESSION_SECRET", "a".repeat(64));
  vi.stubEnv("SESSION_STORE_URL", "redis://:pw@valkey:6379/0");
  vi.stubEnv("GEN9_AGENT_URL", "http://gen9-agent:8000");
  return import("./cookies");
}

afterEach(() => vi.unstubAllEnvs());

describe("ending the session cookie", () => {
  it("over https, repeats __Host-'s Secure and path so the browser takes it (P3-C9)", async () => {
    const { endedSessionCookie, endedTransactionCookie } = await withOrigin("https://gen9.example");
    expect(endedSessionCookie()).toMatchObject({ name: "__Host-gen9_session", value: "", secure: true, path: "/", maxAge: 0 });
    expect(endedTransactionCookie()).toMatchObject({ name: "gen9_auth_txn", secure: true, path: "/auth", maxAge: 0 });
  });

  it("over http, the plain name without Secure", async () => {
    const { endedSessionCookie } = await withOrigin("http://localhost:14000");
    expect(endedSessionCookie()).toMatchObject({ name: "gen9_session", secure: false, path: "/", maxAge: 0 });
  });
});
