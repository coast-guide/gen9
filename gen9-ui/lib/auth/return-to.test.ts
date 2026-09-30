import { describe, expect, it } from "vitest";

import { safeReturnTo } from "./return-to";

// Post-login destinations come from the URL: only same-origin paths may pass (no open redirect)
describe("safeReturnTo", () => {
  it.each([
    ["/chat", "/chat"],
    ["/chat/5f2dc7cf?x=1#top", "/chat/5f2dc7cf?x=1#top"],
    ["/settings", "/settings"],
    ["/%2F%2Fevil.example", "/%2F%2Fevil.example"], // encoded slashes stay a path on this origin
  ])("keeps same-origin path %s", (input, expected) => {
    expect(safeReturnTo(input)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    "",
    "chat",
    "https://evil.example/",
    "//evil.example/path",
    "/\\evil.example",
    "javascript:alert(1)",
    " /chat",
    "/auth/login", // would loop back into sign-in
    "/auth/callback?code=x",
  ])("falls back for %s", (input) => {
    expect(safeReturnTo(input)).toBe("/chat");
  });

  it("uses the given fallback", () => {
    expect(safeReturnTo("https://evil.example", "/settings")).toBe("/settings");
  });
});
