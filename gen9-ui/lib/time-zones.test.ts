import { describe, expect, it } from "vitest";

import { ianaName, sameZone } from "./time-zones";

describe("sameZone", () => {
  it("takes a zone's old and current names as one zone", () => {
    expect(sameZone("Asia/Kolkata", "Asia/Calcutta")).toBe(true);
    expect(sameZone("America/New_York", "US/Eastern")).toBe(true);
  });

  it("tells different zones apart, and an unknown name from anything else", () => {
    expect(sameZone("Asia/Kolkata", "America/New_York")).toBe(false);
    expect(sameZone("Nowhere/Land", "UTC")).toBe(false);
    expect(sameZone("Nowhere/Land", "Nowhere/Land")).toBe(true);
  });
});

describe("ianaName", () => {
  it("names a zone as IANA does, and as Gen9 saves it", () => {
    expect(ianaName("Asia/Calcutta")).toBe("Asia/Kolkata");
    expect(ianaName("Europe/Kiev")).toBe("Europe/Kyiv");
    expect(ianaName("America/Indianapolis")).toBe("America/Indiana/Indianapolis");
  });

  it("leaves IANA's own names and unknown ones as they are, and a renamed zone is still the same zone", () => {
    expect(ianaName("Asia/Kolkata")).toBe("Asia/Kolkata");
    expect(ianaName("Nowhere/Land")).toBe("Nowhere/Land");
    for (const old of ["Asia/Calcutta", "Asia/Saigon", "Pacific/Truk"]) expect(sameZone(ianaName(old), old)).toBe(true);
  });
});
