import { describe, expect, it } from "vitest";

import { sends } from "./composer-keys";

const key = (over: Partial<Parameters<typeof sends>[0]>) => ({ key: "Enter", shiftKey: false, isComposing: false, keyCode: 13, ...over });

describe("the composer's Enter", () => {
  it("sends on Enter, and Shift+Enter makes a new line", () => {
    expect(sends(key({}))).toBe(true);
    expect(sends(key({ shiftKey: true }))).toBe(false);
    expect(sends(key({ key: "a", keyCode: 65 }))).toBe(false);
  });

  it("doesn't send the Enter that confirms an input method's conversion", () => {
    // Chrome, Firefox and today's Safari: still composing
    expect(sends(key({ isComposing: true, keyCode: 229 }))).toBe(false);
    // Safari before WebKit's fix: after compositionend, marked only by keyCode 229
    expect(sends(key({ isComposing: false, keyCode: 229 }))).toBe(false);
  });
});
