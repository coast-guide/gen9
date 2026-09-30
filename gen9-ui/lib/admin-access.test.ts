import { describe, expect, it } from "vitest";

import { orNotAdmin, refusedAsNotAdmin } from "./admin-access";

class Refused extends Error {
  constructor(readonly status: number) {
    super(`refused ${status}`);
  }
}

describe("admin access removed after sign-in", () => {
  it("reads a 403 from the API as no longer an admin", () => {
    expect(refusedAsNotAdmin(new Refused(403))).toBe(true);
    expect(refusedAsNotAdmin(new Refused(500))).toBe(false);
    expect(refusedAsNotAdmin(new Error("network"))).toBe(false);
    expect(refusedAsNotAdmin(null)).toBe(false);
  });

  it("gives the answer, null for a 403, and lets other failures through", async () => {
    expect(await orNotAdmin(Promise.resolve(["ada"]))).toEqual(["ada"]);
    expect(await orNotAdmin(Promise.reject(new Refused(403)))).toBeNull();
    await expect(orNotAdmin(Promise.reject(new Refused(503)))).rejects.toThrow("refused 503");
  });
});
