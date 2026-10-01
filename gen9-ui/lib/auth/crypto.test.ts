import { beforeEach, describe, expect, it, vi } from "vitest";

const SECRET_A = "a".repeat(64);
const SECRET_B = "b".repeat(64);

async function load(secret: string) {
  vi.resetModules();
  vi.stubEnv("APP_URL", "http://localhost:14000");
  vi.stubEnv("KEYCLOAK_ISSUER", "http://localhost:15000/realms/gen9");
  vi.stubEnv("KEYCLOAK_CLIENT_ID", "gen9-ui");
  vi.stubEnv("KEYCLOAK_CLIENT_SECRET", "test");
  vi.stubEnv("SESSION_SECRET", secret);
  vi.stubEnv("SESSION_STORE_URL", "redis://localhost:6379");
  vi.stubEnv("GEN9_AGENT_URL", "http://localhost:17000");
  return import("./crypto");
}

describe("session record sealing (AES-256-GCM)", () => {
  beforeEach(() => vi.unstubAllEnvs());

  it("round-trips a record", async () => {
    const { seal, unseal } = await load(SECRET_A);
    const record = { sub: "user-1", refreshToken: "secret-refresh-token" };
    const sealed = seal(record, "gen9:session:a");
    expect(sealed).not.toContain("secret-refresh-token");
    expect(unseal(sealed, "gen9:session:a")).toEqual(record);
  });

  it("uses a fresh IV every time", async () => {
    const { seal } = await load(SECRET_A);
    expect(seal({ a: 1 }, "k")).not.toBe(seal({ a: 1 }, "k"));
  });

  it("rejects a tampered record", async () => {
    const { seal, unseal } = await load(SECRET_A);
    const raw = Buffer.from(seal({ sub: "user-1" }, "k"), "base64url");
    raw[raw.length - 1] ^= 0x01;
    expect(unseal(raw.toString("base64url"), "k")).toBeNull();
  });

  // P7-C2: someone who can write to Valkey can't take over a session by copying its record
  it("rejects a record copied under another key", async () => {
    const { seal, unseal } = await load(SECRET_A);
    const sealed = seal({ sub: "user-1" }, "gen9:session:victim");
    expect(unseal(sealed, "gen9:session:attacker")).toBeNull();
    expect(unseal(sealed, "gen9:session:victim")).toEqual({ sub: "user-1" });
  });

  it("rejects records sealed with another secret (rotation signs everyone out)", async () => {
    const sealed = (await load(SECRET_A)).seal({ sub: "user-1" }, "k");
    expect((await load(SECRET_B)).unseal(sealed, "k")).toBeNull();
  });
});

describe("session ids", () => {
  it("are 256-bit, URL-safe and unique", async () => {
    const { randomId } = await load(SECRET_A);
    const ids = new Set(Array.from({ length: 1000 }, randomId));
    expect(ids.size).toBe(1000);
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("are stored as a hash, never as the cookie value", async () => {
    const { hashId, randomId } = await load(SECRET_A);
    const id = randomId();
    expect(hashId(id)).not.toBe(id);
    expect(hashId(id)).toBe(hashId(id));
  });
});
