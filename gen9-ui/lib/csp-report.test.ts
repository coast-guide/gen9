import { describe, expect, it } from "vitest";

import { readBounded } from "./bounded-body";
import { line, violations } from "./csp-report";

describe("CSP violation reports", () => {
  it("reads the Reporting API's format, keeping only origins and paths", () => {
    const found = violations("application/reports+json", [
      {
        type: "csp-violation",
        body: {
          effectiveDirective: "img-src",
          blockedURL: "https://attacker.example/pixel.png?card=LIB-1234",
          documentURL: "http://localhost:14000/chat/abc?q=secret",
        },
      },
      { type: "deprecation", body: {} },
    ]);
    expect(found.map(line)).toEqual(["[csp] img-src blocked https://attacker.example/pixel.png on /chat/abc"]);
  });

  it("reads report-uri's older format, and inline or data sources by name", () => {
    expect(
      violations("application/csp-report", {
        "csp-report": { "violated-directive": "script-src-elem", "blocked-uri": "inline", "document-uri": "http://localhost:14000/settings" },
      }).map(line),
    ).toEqual(["[csp] script-src-elem blocked inline on /settings"]);
    expect(violations("application/csp-report", { "csp-report": { "effective-directive": "img-src", "blocked-uri": "data:image/png;base64,AAAA" } })[0].blocked).toBe("data");
  });

  it("logs one line per report, whatever a caller puts in it (M9, U2)", () => {
    const forged = violations("application/reports+json", [
      { type: "csp-violation", body: { effectiveDirective: "img-src\n[csp] script-src blocked https://forged.example on /settings", blockedURL: "inline\n[csp] forged", documentURL: "http://localhost:14000/chat" } },
    ]).map(line);
    expect(forged).toEqual(["[csp] (directive?) blocked inline?[csp] forged on /chat"]);
    expect(forged.every((l) => !/[\u0000-\u001f]/.test(l))).toBe(true);
    expect(violations("application/csp-report", { "csp-report": { "violated-directive": "img-src 'self'", "blocked-uri": "eval" } }).map(line)).toEqual(["[csp] img-src blocked eval on (unknown page)"]);
  });

  it("takes nothing else", () => {
    expect(violations("application/json", { hello: "world" })).toEqual([]);
    expect(violations("application/reports+json", { not: "a list" })).toEqual([]);
    expect(violations("application/csp-report", null)).toEqual([]);
  });
});

describe("reading a report's body", () => {
  const stream = (...parts: string[]) =>
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const part of parts) controller.enqueue(new TextEncoder().encode(part));
        controller.close();
      },
    });

  it("gives the text within the bound, and nothing past it without reading the rest", async () => {
    expect(await readBounded(stream('{"a":', "1}"), 64)).toBe('{"a":1}');
    let pulled = 0;
    const endless = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled++;
        controller.enqueue(new Uint8Array(1024));
      },
    });
    expect(await readBounded(endless, 4096)).toBeNull();
    expect(pulled).toBeLessThan(10);
    expect(await readBounded(null, 64)).toBe("");
  });
});
