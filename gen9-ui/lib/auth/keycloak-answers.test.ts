import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({
  env: () => ({ KEYCLOAK_ISSUER: "http://localhost:15000/realms/gen9", KEYCLOAK_INTERNAL_URL: "http://gen9-keycloak:8080" }),
}));

import { keycloakAnswers } from "@/lib/auth/oidc";

afterEach(() => vi.unstubAllGlobals());

describe("keycloakAnswers", () => {
  it("asks Keycloak's discovery document on the back-channel", async () => {
    const fetch = vi.fn<(url: string) => Promise<Response>>(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await keycloakAnswers()).toBe(true);
    expect(fetch.mock.calls[0][0]).toBe("http://gen9-keycloak:8080/realms/gen9/.well-known/openid-configuration");
  });

  it("is false when Keycloak is down or failing (P2-J5)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    expect(await keycloakAnswers()).toBe(false);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    expect(await keycloakAnswers()).toBe(false);
  });
});
