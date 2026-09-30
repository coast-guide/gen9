// The MCP Apps sandbox proxy (MCP Apps 2026-01-26, "Sandbox proxy"): a web host must put a page on
// another origin between itself and a connector's View. This server serves that page and nothing
// else, from the web app's image (`node sandbox/server.ts`: Node 24 strips the types), on its own port (compose.yaml, service `sandbox`).
//
// - Any host name: the web app gives each MCP server its own origin (MCP_APPS_SANDBOX_URL,
//   `http://{id}.apps.localhost:14003` locally), so one server's View can't read another's storage.
// - Its CSP is an HTTP header built from `?csp=`, the domains the View declared (`_meta.ui.csp`),
//   so the View can't loosen it: the spec's construction, nothing undeclared, `frame-ancestors`
//   the web app only.
// - The page takes messages from the web app only, writes the View into an inner frame (which
//   inherits the CSP), and relays JSON-RPC between the two (`ui/notifications/sandbox-*` stay
//   between the web app and this page).
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

const PORT = Number(process.env.PORT ?? 3001);
const APP = new URL(process.env.APP_URL ?? "http://localhost:14000").origin;

// A CSP source for a declared domain: scheme, host (a leading `*.` allowed), port, a plain path.
// Anything else (quotes, `;`, spaces, keywords) is dropped, so a View can't inject a directive
const SOURCE = /^(https?|wss?):\/\/(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*(:\d{1,5})?(\/[A-Za-z0-9._~%/-]*)?$/;
const sources = (list: unknown): string[] =>
  Array.isArray(list) ? list.filter((d): d is string => typeof d === "string" && SOURCE.test(d)).slice(0, 32) : [];

/** The domains a View declares (`McpUiResourceCsp`). */
export type ViewCsp = { connectDomains?: unknown; resourceDomains?: unknown; frameDomains?: unknown; baseUriDomains?: unknown } | null;

/** The View's CSP (the spec's "Content Security Policy Enforcement"), from its declared domains. */
export function contentSecurityPolicy(csp: ViewCsp): string {
  const resources = sources(csp?.resourceDomains).join(" ");
  const connects = sources(csp?.connectDomains).join(" ");
  const frames = sources(csp?.frameDomains).join(" ");
  const bases = sources(csp?.baseUriDomains).join(" ");
  return [
    "default-src 'none'",
    `script-src 'self' 'unsafe-inline' ${resources}`.trim(),
    `style-src 'self' 'unsafe-inline' ${resources}`.trim(),
    `img-src 'self' data: ${resources}`.trim(),
    `font-src 'self' ${resources}`.trim(),
    `media-src 'self' data: ${resources}`.trim(),
    // Nothing unless declared (the spec's restrictive default)
    `connect-src ${connects || "'none'"}`,
    `frame-src ${frames || "'none'"}`,
    "object-src 'none'",
    `base-uri ${bases || "'self'"}`,
    // Only the web app may frame this page
    `frame-ancestors ${APP}`,
  ].join("; ");
}

const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Gen9 app sandbox</title>
<style>html,body{margin:0;height:100%;overflow:hidden;background:transparent}iframe{display:block;width:100%;height:100%;border:0}</style>
</head>
<body>
<script>
(() => {
  const HOST = ${JSON.stringify(APP)};
  const OWN = location.origin;
  const SANDBOX = "ui/notifications/sandbox-";
  if (window.self === window.top) throw new Error("This page runs inside Gen9 only.");
  if (!document.referrer || new URL(document.referrer).origin !== HOST) throw new Error("Not embedded by Gen9.");
  const inner = document.createElement("iframe");
  inner.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
  inner.title = "App";
  document.body.appendChild(inner);
  addEventListener("message", (event) => {
    const data = event.data;
    const method = data && typeof data.method === "string" ? data.method : "";
    if (event.source === window.parent) {
      if (event.origin !== HOST) return;
      if (method === SANDBOX + "resource-ready") {
        const html = data.params && data.params.html;
        if (typeof html !== "string") return;
        const doc = inner.contentDocument;
        doc.open();
        doc.write(html);
        doc.close();
        return;
      }
      if (!method.startsWith(SANDBOX) && inner.contentWindow) inner.contentWindow.postMessage(data, OWN);
    } else if (event.source === inner.contentWindow) {
      if (event.origin !== OWN || method.startsWith(SANDBOX)) return;
      window.parent.postMessage(data, HOST);
    }
  });
  window.parent.postMessage({ jsonrpc: "2.0", method: SANDBOX + "proxy-ready", params: {} }, HOST);
})();
</script>
</body>
</html>
`;

function serve(request: IncomingMessage, response: ServerResponse) {
  const url = new URL(request.url ?? "/", "http://sandbox");
  if (request.method === "GET" && url.pathname === "/health") {
    response.writeHead(200, { "Content-Type": "text/plain" }).end("ok");
    return;
  }
  if (request.method !== "GET" || url.pathname !== "/") {
    response.writeHead(404, { "Content-Type": "text/plain" }).end("Only the sandbox page is served here.");
    return;
  }
  let csp: ViewCsp;
  try {
    csp = JSON.parse(url.searchParams.get("csp") ?? "null");
  } catch {
    csp = null;
  }
  response.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Security-Policy": contentSecurityPolicy(csp),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
  response.end(PAGE);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  createServer(serve).listen(PORT, "0.0.0.0", () => console.log(`sandbox on :${PORT}, framed by ${APP}`));
}
