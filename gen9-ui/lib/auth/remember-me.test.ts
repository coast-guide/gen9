import { describe, expect, it } from "vitest";

import { ORDINARY_SESSION_S, rememberedSession } from "./remember-me";

describe("rememberedSession", () => {
  it("is false for an ordinary session: its refresh token lives 30 minutes idle", () => {
    expect(rememberedSession(1800)).toBe(false);
    expect(rememberedSession(ORDINARY_SESSION_S)).toBe(false);
    expect(rememberedSession(0)).toBe(false);
  });

  it("is true for a remembered one: 14 days idle", () => {
    expect(rememberedSession(1_209_600)).toBe(true);
  });
});
