import { type NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE_NAMES } from "@/lib/auth/cookie-names";
import { PAGE_HEADER } from "@/lib/auth/page-header";

// Every page of the app: signed out, it sends to sign-in and back to the same page
const PROTECTED = /^\/(chat|search|scheduled|settings|admin)(\/|$)/;

/**
 * Content-Security-Policy with a per-request nonce (Next.js 16 CSP guide): only scripts Next.js
 * renders with this nonce run, which blunts XSS, the main threat to a cookie-based session.
 * `form-action` also allows Keycloak: sign-out POSTs here, then redirects to Keycloak's logout.
 */
// Where browsers send what the policy blocked (app/api/csp-report), as an absolute address.
// report-uri always; report-to (the Reporting API, which MDN marks Baseline 2026) only over https:
// probed in Chrome 153, it delivers nothing to an http endpoint, and its presence silences
// report-uri, so over http no report came at all (manual-e2e.md, P3-F3)
const reportsTo = (request: NextRequest) => new URL("/api/csp-report", process.env.APP_URL ?? request.nextUrl.origin).href;
const REPORTING_API = process.env.APP_URL?.startsWith("https://") ?? false;

function contentSecurityPolicy(nonce: string, request: NextRequest): string {
  const dev = process.env.NODE_ENV === "development";
  const https = request.nextUrl.protocol === "https:";
  const keycloak = process.env.KEYCLOAK_ISSUER ? new URL(process.env.KEYCLOAK_ISSUER).origin : "";
  // Connectors' Views, each on its own origin under the sandbox's (MCP Apps, lib/apps.ts)
  const sandbox = process.env.MCP_APPS_SANDBOX_URL?.replaceAll("{id}", "*");
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    // Inline style attributes come from UI primitives (positioning); no inline <style> injection
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    `connect-src 'self'${dev ? " ws:" : ""}`,
    `frame-src ${sandbox ?? "'none'"}`,
    "object-src 'none'",
    "base-uri 'self'",
    `form-action 'self' ${keycloak}`.trim(),
    "frame-ancestors 'none'",
    ...(https ? ["upgrade-insecure-requests"] : []),
    // What it blocks is reported to app/api/csp-report, which logs it (reportsTo, above)
    `report-uri ${reportsTo(request)}`,
    ...(REPORTING_API ? ["report-to csp"] : []),
  ].join("; ");
}

// Over https, browsers keep to it for two years (HSTS: Next.js's security headers guide, OWASP's
// HTTP Strict Transport Security cheat sheet; `preload` is a deployer's choice). Decided when the
// server runs, from APP_URL, which the image doesn't know when it is built; plain http sends none
const HSTS = process.env.APP_URL?.startsWith("https://") ? "max-age=63072000; includeSubDomains" : null;

function secured(response: NextResponse): NextResponse {
  if (HSTS) response.headers.set("Strict-Transport-Security", HSTS);
  return response;
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // Optimistic check only (Next.js 16 guidance): no session cookie on an app page -> sign in first.
  // It never trusts the cookie; pages verify the session in the data access layer.
  if (PROTECTED.test(pathname) && !SESSION_COOKIE_NAMES.some((name) => request.cookies.has(name))) {
    const login = new URL("/auth/login", request.url);
    login.searchParams.set("returnTo", `${pathname}${search}`);
    return secured(NextResponse.redirect(login));
  }

  // 128 random bits, as CSP asks of a nonce and OWASP ASVS 5.0 (11.5.1) of anything unguessable;
  // Next.js's guide's crypto.randomUUID() has 122 (docs/plans/manual-e2e.md, P7-C2)
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString("base64");
  const csp = contentSecurityPolicy(nonce, request);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  // The page asked for, where sign-in brings the person back to (lib/agent.ts)
  requestHeaders.set(PAGE_HEADER, `${pathname}${search}`);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  if (REPORTING_API) response.headers.set("Reporting-Endpoints", `csp="${reportsTo(request)}"`);
  return secured(response);
}

export const config = {
  // Pages only: not route handlers (/api, /auth but its error page), static assets or metadata
  // files. /auth/error is a page: without this it was served with no CSP (manual-e2e.md, P7-E2)
  matcher: [
    {
      source: "/((?!api/|auth/(?!error)|_next/static|_next/image|brand/|favicon.ico|icon.svg|apple-icon.png|manifest.webmanifest).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
