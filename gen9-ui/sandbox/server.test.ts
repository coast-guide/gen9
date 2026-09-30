import { describe, expect, it } from "vitest";

import { contentSecurityPolicy } from "./server";

const directive = (csp: string, name: string) => csp.split("; ").find((d) => d.startsWith(`${name} `));

describe("the View's CSP", () => {
  it("is the spec's restrictive default when the View declares nothing", () => {
    const csp = contentSecurityPolicy(null);
    expect(directive(csp, "default-src")).toBe("default-src 'none'");
    expect(directive(csp, "connect-src")).toBe("connect-src 'none'");
    expect(directive(csp, "frame-src")).toBe("frame-src 'none'");
    expect(directive(csp, "object-src")).toBe("object-src 'none'");
    expect(directive(csp, "frame-ancestors")).toBe("frame-ancestors http://localhost:14000");
  });

  it("allows the declared domains where each belongs", () => {
    const csp = contentSecurityPolicy({ connectDomains: ["https://api.example.com"], resourceDomains: ["https://*.cdn.example.com"] });
    expect(directive(csp, "connect-src")).toBe("connect-src https://api.example.com");
    expect(directive(csp, "script-src")).toBe("script-src 'self' 'unsafe-inline' https://*.cdn.example.com");
    expect(directive(csp, "img-src")).toContain("https://*.cdn.example.com");
  });

  it("drops anything that isn't a plain source, so a View can't add a directive or a keyword", () => {
    const csp = contentSecurityPolicy({
      connectDomains: ["https://ok.example.com", "https://x.com; script-src *", "'unsafe-eval'", "*", "https://a.com https://b.com", 42],
      frameDomains: "https://not-a-list.example.com",
    });
    expect(directive(csp, "connect-src")).toBe("connect-src https://ok.example.com");
    expect(directive(csp, "frame-src")).toBe("frame-src 'none'");
    expect(csp.match(/script-src/g)).toHaveLength(1);
  });
});
