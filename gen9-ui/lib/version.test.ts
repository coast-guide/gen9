import { describe, expect, it } from "vitest";

import { versionWords } from "./version";

describe("versionWords", () => {
  it("names the version and the commit's first seven characters", () => {
    expect(versionWords({ version: "0.1.0", commit: "0123abcdef4567890123abcdef4567890123abcd" })).toBe("0.1.0, commit 0123abc");
  });
  it("says when the images were built here, with no commit", () => {
    expect(versionWords({ version: "0.1.0", commit: "" })).toBe("0.1.0, built from local code");
  });
});
