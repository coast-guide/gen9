import { describe, expect, it } from "vitest";

import { CHECK_INPUT, refusal } from "./refusal";

describe("refusal", () => {
  it("shows gen9-agent's own sentence", () => {
    expect(refusal("At most 20 tasks at a time. Delete one first.", "Try again.")).toBe("At most 20 tasks at a time. Delete one first.");
  });

  it("takes Gen9's words from a validation list, and nothing meant for developers", () => {
    const blank = [
      { type: "value_error", loc: ["body", "name"], msg: "Value error, Name the task.", input: "" },
      { type: "value_error", loc: ["body", "prompt"], msg: "Value error, Say what Gen9 should do.", input: "" },
    ];
    expect(refusal(blank, "Try again.")).toBe("Name the task.");
    const schema = [{ type: "string_too_long", loc: ["body", "name"], msg: "String should have at most 80 characters" }];
    expect(refusal(schema, "Try again.")).toBe(CHECK_INPUT);
    expect(refusal([null, 3], "Try again.")).toBe(CHECK_INPUT);
  });

  it("falls back when there is nothing to say", () => {
    for (const detail of [undefined, null, "", "  ", { detail: "nested" }, 422]) expect(refusal(detail, "Couldn’t attach it.")).toBe("Couldn’t attach it.");
  });
});
