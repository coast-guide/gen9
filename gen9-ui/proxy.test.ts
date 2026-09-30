import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { SESSION_COOKIE_NAMES } from "./lib/auth/cookie-names";
import { PAGE_HEADER } from "./lib/auth/page-header";
import { proxy } from "./proxy";

describe("proxy", () => {
  it.each(["/chat", "/chat/abc", "/search?q=lisbon&mode=keyword", "/scheduled", "/settings", "/admin/users"])(
    "sends %s to sign-in and back to the same page when signed out",
    (path) => {
      const response = proxy(new NextRequest(`http://localhost:14000${path}`));
      expect(response.status).toBe(307);
      const location = new URL(response.headers.get("location") ?? "");
      expect(location.pathname).toBe("/auth/login");
      expect(location.searchParams.get("returnTo")).toBe(path);
    },
  );

  it("names the page for a return after sign-in, whatever the browser sent", () => {
    const request = new NextRequest("http://localhost:14000/admin/plugins?x=1", {
      headers: { cookie: `${SESSION_COOKIE_NAMES[0]}=some-session`, [PAGE_HEADER]: "https://evil.example" },
    });
    const response = proxy(request);
    expect(response.headers.get("location")).toBeNull();
    // How Next.js passes the proxy's request headers on to the page
    expect(response.headers.get(`x-middleware-request-${PAGE_HEADER}`)).toBe("/admin/plugins?x=1");
  });

  it("lets the landing page and the signed-out page through, with a CSP", () => {
    for (const path of ["/", "/signed-out"]) {
      const response = proxy(new NextRequest(`http://localhost:14000${path}`));
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("content-security-policy")).toContain("frame-ancestors 'none'");
    }
  });
});
