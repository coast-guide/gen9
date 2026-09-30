import { describe, expect, it } from "vitest";

import { lengthNote, MAX_MESSAGE_CHARS } from "./message-length";

describe("lengthNote", () => {
  it("says nothing well under the limit", () => {
    expect(lengthNote(0)).toBeNull();
    expect(lengthNote(7199)).toBeNull();
  });

  it("counts down near the limit, then counts what's too many", () => {
    expect(lengthNote(7200)).toEqual({ text: "800 characters left", over: false });
    expect(lengthNote(MAX_MESSAGE_CHARS)).toEqual({ text: "0 characters left", over: false });
    expect(lengthNote(MAX_MESSAGE_CHARS + 1)).toEqual({ text: "1 character too many", over: true });
    expect(lengthNote(20_000)).toEqual({ text: "12,000 characters too many", over: true });
  });
});
