import { describe, expect, it } from "vitest";

import { messageText, replacesDraft, settled, SETTLE_MS } from "./app-asks";

describe("settled", () => {
  it("counts a click only once the ask has stayed put for SETTLE_MS", () => {
    expect(settled(1000, 1000)).toBe(false);
    expect(settled(1000, 1000 + SETTLE_MS - 1)).toBe(false);
    expect(settled(1000, 1000 + SETTLE_MS)).toBe(true);
  });
});

describe("replacesDraft", () => {
  it("goes straight into an empty composer, or one holding the same text", () => {
    expect(replacesDraft("", "Tell me about cell 1")).toBe(false);
    expect(replacesDraft("  \n", "Tell me about cell 1")).toBe(false);
    expect(replacesDraft("Tell me about cell 1", "Tell me about cell 1")).toBe(false);
  });

  it("asks before replacing what the person wrote", () => {
    expect(replacesDraft("please look at the board", "Tell me about cell 1")).toBe(true);
  });
});

describe("messageText", () => {
  it("joins the text blocks, one block or many", () => {
    expect(messageText({ type: "text", text: "one" })).toBe("one");
    expect(messageText([{ type: "text", text: "one" }, { type: "text", text: "two" }])).toBe("one\ntwo");
  });

  it("leaves out what isn't text", () => {
    expect(messageText([{ type: "image", data: "…" }, null, { type: "text", text: 3 }, { type: "text", text: "kept" }])).toBe("kept");
    expect(messageText(undefined)).toBe("");
  });
});
