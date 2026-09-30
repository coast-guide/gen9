import { describe, expect, it } from "vitest";

import { codeOf, hasHidden, reveal } from "./reveal";

// Hidden characters are written as escapes here, never raw: a raw one in source is what this guards against
describe("reveal", () => {
  it("leaves plain text, tabs and newlines as they are", () => {
    expect(reveal("ls -la\n\tcat notes.txt")).toEqual([{ text: "ls -la\n\tcat notes.txt" }]);
    expect(hasHidden("ls -la\n\tcat notes.txt")).toBe(false);
    expect(hasHidden("r\u00e9pertoire \u65e5\u672c\u8a9e \u0645\u0631\u062d\u0628\u0627")).toBe(false);
  });

  it("shows a right-to-left override where it is, so the command reads as it runs", () => {
    // Trojan Source: a right-to-left override makes the rest of the line read backwards
    expect(reveal("echo safe #\u202etxt")).toEqual([{ text: "echo safe #" }, { hidden: "\u202e", code: "U+202E" }, { text: "txt" }]);
    expect(hasHidden("a\u2066b\u2069")).toBe(true);
  });

  it("shows zero-width characters, the byte order mark, escapes and separators", () => {
    const codes = (text: string) => reveal(text).flatMap((p) => ("code" in p ? [p.code] : []));
    expect(codes("rm\u200b -rf")).toEqual(["U+200B"]);
    expect(codes("\ufeffx\u2060y")).toEqual(["U+FEFF", "U+2060"]);
    expect(codes("ok\u001b[2K\rsafe")).toEqual(["U+001B", "U+000D"]);
    expect(codes("a\u2028b\u2029c")).toEqual(["U+2028", "U+2029"]);
  });

  it("names a character by its code point", () => {
    expect(codeOf("\u200e")).toBe("U+200E");
    expect(codeOf("\u{E0041}")).toBe("U+E0041");
  });
});
