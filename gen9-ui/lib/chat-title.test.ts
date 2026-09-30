import { describe, expect, it } from "vitest";

import { chatTitle } from "./chat-title";

describe("a chat's tab title", () => {
  it("is its title or first message, on one line", () => {
    expect(chatTitle("Porto weekend")).toBe("Porto weekend");
    expect(chatTitle("Reply with\n  one word:  pong")).toBe("Reply with one word: pong");
  });

  it("is cut to 60 characters with an ellipsis", () => {
    const title = chatTitle("x".repeat(80));
    expect(title).toHaveLength(60);
    expect(title.endsWith("…")).toBe(true);
  });

  it("says Chat when there is nothing to name it by", () => {
    expect(chatTitle(null)).toBe("Chat");
    expect(chatTitle("   ")).toBe("Chat");
  });
});
