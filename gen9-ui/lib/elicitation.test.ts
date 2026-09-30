import { describe, expect, it } from "vitest";

import { addressParts, contentOf, fieldsOf, isWebAddress } from "@/lib/elicitation";

const schema = {
  type: "object" as const,
  properties: {
    city: { type: "string" as const, title: "City", minLength: 2 },
    nights: { type: "integer" as const, minimum: 1, maximum: 30, default: 2 },
    email: { type: "string" as const, format: "email" as const },
    window: { type: "boolean" as const, default: true },
    class: { type: "string" as const, oneOf: [{ const: "eco", title: "Economy" }, { const: "biz", title: "Business" }] },
    extras: { type: "array" as const, items: { enum: ["wifi", "meals"] }, maxItems: 2 },
  },
  required: ["city", "nights"],
};

describe("a server's form", () => {
  it("becomes fields of the right kind, with labels, limits and defaults", () => {
    const fields = fieldsOf(schema);
    expect(fields.map((f) => [f.name, f.kind, f.required])).toEqual([
      ["city", "text", true],
      ["nights", "integer", true],
      ["email", "email", false],
      ["window", "boolean", false],
      ["class", "choice", false],
      ["extras", "choices", false],
    ]);
    expect(fields[0].label).toBe("City");
    expect(fields[1]).toMatchObject({ min: 1, max: 30, initial: "2" });
    expect(fields[3].initial).toBe(true);
    expect(fields[4].choices).toEqual([{ value: "eco", label: "Economy" }, { value: "biz", label: "Business" }]);
    expect(fields[5].choices.map((c) => c.value)).toEqual(["wifi", "meals"]);
  });

  it("follows the server's order when given", () => {
    expect(fieldsOf(schema, ["nights", "city"]).map((f) => f.name)).toEqual(["nights", "city", "email", "window", "class", "extras"]);
  });

  it("sends what was entered, typed, and leaves empty optional fields out", () => {
    const fields = fieldsOf(schema);
    expect(contentOf(fields, { city: " Lisbon ", nights: "3", email: "", window: false, class: "biz", extras: [] })).toEqual({
      city: "Lisbon",
      nights: 3,
      window: false,
      class: "biz",
    });
  });
});

describe("an address to open", () => {
  it("shows its host apart, and notices punycode", () => {
    expect(addressParts("https://calendar.example.com/connect?x=1")).toEqual({ before: "https://", host: "calendar.example.com", after: "/connect?x=1", punycode: false });
    expect(addressParts("https://xn--pple-43d.com/login").punycode).toBe(true);
  });

  it("opens only a web page: never javascript:, file: or another app's scheme (M9, U1)", () => {
    expect(isWebAddress("https://calendar.example.com/connect")).toBe(true);
    expect(isWebAddress("http://localhost:3000/x")).toBe(true);
    for (const bad of ["javascript:alert(document.domain)", "JavaScript:alert(1)", "file:///etc/passwd", "vscode://x", "data:text/html,hi", "not a url"]) {
      expect(isWebAddress(bad)).toBe(false);
    }
  });
});
