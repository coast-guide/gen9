import { describe, expect, it } from "vitest";

import { limitWords } from "./limit";

describe("limitWords", () => {
  it("says how much of the limit is used and when it resets", () => {
    expect(limitWords({ limited: true, used: 0.0033, resets_at: "2026-10-01T00:00:00Z" })).toBe(
      "Less than 1% of your limit, which resets on 1 October 2026",
    );
    expect(limitWords({ limited: true, used: 0.426, resets_at: "2026-10-31T00:00:00Z" })).toBe("43% of your limit, which resets on 31 October 2026");
    expect(limitWords({ limited: true, used: 1.02, resets_at: "2026-10-01T00:00:00Z" })).toBe("All of your limit: it resets on 1 October 2026");
  });

  it("says when there's no limit, or no reset known", () => {
    expect(limitWords({ limited: false, used: null, resets_at: null })).toBe("No limit");
    expect(limitWords({ limited: true, used: 0.5, resets_at: null })).toBe("50% of your limit");
  });
});
