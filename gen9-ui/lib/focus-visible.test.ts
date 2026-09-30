import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Tailwind 4's `outline-none` removes the focus outline even in forced colours (Windows' contrast themes), where the
// rings drawn with box-shadow are dropped too: keyboard focus disappeared from most controls (found by hand: P2-J2).
// `outline-hidden` looks the same and keeps the outline there. shadcn's components come with `outline-none`.
function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "node_modules" || entry.name.startsWith(".") ? [] : sources(path);
    return /\.(tsx?|css)$/.test(entry.name) && !entry.name.endsWith(".test.ts") ? [path] : [];
  });
}

describe("focus stays visible in forced colours", () => {
  it("uses outline-hidden, never outline-none", () => {
    const offenders = ["app", "components", "lib"].flatMap(sources).filter((file) => /(^|[^\w-])outline-none\b/.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });
});
