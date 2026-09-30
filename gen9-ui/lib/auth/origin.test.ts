import { beforeEach, describe, expect, it, vi } from "vitest";

describe("isSameOrigin (CSRF check for state-changing route handlers)", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("APP_URL", "http://localhost:14000");
    vi.stubEnv("KEYCLOAK_ISSUER", "http://localhost:15000/realms/gen9");
    vi.stubEnv("KEYCLOAK_CLIENT_ID", "gen9-ui");
    vi.stubEnv("KEYCLOAK_CLIENT_SECRET", "test");
    vi.stubEnv("SESSION_SECRET", "a".repeat(64));
    vi.stubEnv("SESSION_STORE_URL", "redis://localhost:6379");
    vi.stubEnv("GEN9_AGENT_URL", "http://localhost:17000");
  });

  const post = (origin?: string) =>
    new Request("http://localhost:14000/auth/logout", { method: "POST", headers: origin ? { origin } : {} });

  it("accepts this app's origin", async () => {
    const { isSameOrigin } = await import("./origin");
    expect(isSameOrigin(post("http://localhost:14000"))).toBe(true);
  });

  it.each([undefined, "http://evil.example", "http://localhost:14001", "null"])("rejects origin %s", async (origin) => {
    const { isSameOrigin } = await import("./origin");
    expect(isSameOrigin(post(origin))).toBe(false);
  });
});
