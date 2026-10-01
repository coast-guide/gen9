import { describe, expect, it } from "vitest";

import { relativeText } from "./relative-time";

const now = Date.parse("2026-10-01T10:00:00Z");
const minutes = (n: number) => n * 60_000;

describe("relativeText", () => {
  it("says how long ago a past time was, and how long until a future one", () => {
    expect(relativeText(now - minutes(5), now, "past", "en")).toBe("5 minutes ago");
    expect(relativeText(now - minutes(60 * 26), now, "past", "en")).toBe("yesterday");
    expect(relativeText(now + minutes(60 * 3), now, "future", "en")).toBe("in 3 hours");
  });

  it("says just now, or in under a minute, inside a minute", () => {
    expect(relativeText(now - 30_000, now, "past", "en")).toBe("just now");
    expect(relativeText(now + 30_000, now, "future", "en")).toBe("in under a minute");
  });

  it("reads a time on the wrong side of now, from a clock that is off, as now", () => {
    // Done a moment ago by the server's clock, 3 minutes ahead of the viewer's
    expect(relativeText(now + minutes(3), now, "past", "en")).toBe("just now");
    // A task's next run, due by the server's clock, the viewer's clock 3 minutes ahead
    expect(relativeText(now - minutes(3), now, "future", "en")).toBe("now");
  });
});
