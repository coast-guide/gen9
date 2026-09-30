import { describe, expect, it } from "vitest";

import { secretRefusal } from "@/lib/secret-refusal";

const refused = (field: string, msg: string) => ({ detail: [{ loc: ["body", field], msg: `Value error, ${msg}`, type: "value_error" }] });

describe("secretRefusal", () => {
  it("says what to change at the field the API refused (P2-K2)", () => {
    expect(secretRefusal(refused("host", "a host name, not an IP address"))).toEqual({ field: "host", error: "Use the host's name, not an IP address." });
    expect(secretRefusal(refused("host", "String should match pattern"))).toEqual({
      field: "host",
      error: "Use a host name like api.github.com, without https:// or a port.",
    });
    expect(secretRefusal(refused("header", "name the header it goes in")).field).toBe("header");
    expect(secretRefusal(refused("value", "Basic takes user:password"))).toEqual({ field: "value", error: "Basic takes user:password." });
    expect(secretRefusal(refused("path", "String should match pattern")).field).toBe("path");
    expect(secretRefusal(refused("name", "String should match pattern")).field).toBe("name");
  });

  it("falls back to one sentence when no field is named", () => {
    expect(secretRefusal({ detail: "Something" })).toEqual({ error: "Gen9 couldn’t take that secret. Check each field and try again." });
    expect(secretRefusal(null).field).toBeUndefined();
  });
});
