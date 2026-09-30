// Probe: Keycloak 26.7.4's Client ID Metadata Documents (--features=cimd, experimental), on a
// throwaway container (port 18999, realm `probe`; setup in NOTES.md). A client identified by the
// URL of its metadata document runs the authorization code flow with PKCE: Keycloak fetches the
// document from host.docker.internal, Puppeteer signs in and consents, the code is exchanged.
//   node explore/mcp_server/cimd_probe.mjs   (from gen9-agent; uses e2e's puppeteer)
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../../e2e/package.json", import.meta.url));
const puppeteer = require("puppeteer-core");
const KC = "http://localhost:18999/realms/probe";
const CHROME = process.env.CHROME_PATH ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "/usr/bin/google-chrome");

const listen = (server) => new Promise((resolve) => server.listen(0, "0.0.0.0", () => resolve(server.address().port)));
const callback = createServer();
const cbPort = await listen(callback);
const REDIRECT = `http://127.0.0.1:${cbPort}/callback`;
const docs = createServer();
const docPort = await listen(docs);
const CLIENT_ID = `http://host.docker.internal:${docPort}/client.json`;
let fetched = 0;
docs.on("request", (req, res) => {
  fetched++;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify({ client_id: CLIENT_ID, client_name: "CIMD probe", redirect_uris: [REDIRECT], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none" }));
});
let gotCode;
const code = new Promise((r) => (gotCode = r));
callback.on("request", (req, res) => {
  const url = new URL(req.url, REDIRECT);
  gotCode({ code: url.searchParams.get("code"), error: url.searchParams.get("error"), iss: url.searchParams.get("iss") });
  res.end("ok");
});

const verifier = randomBytes(32).toString("base64url");
const challenge = createHash("sha256").update(verifier).digest("base64url");
const auth = new URL(`${KC}/protocol/openid-connect/auth`);
for (const [k, v] of Object.entries({ client_id: CLIENT_ID, response_type: "code", redirect_uri: REDIRECT, scope: "openid gen9-mcp", code_challenge: challenge, code_challenge_method: "S256", resource: "http://localhost:17000/mcp", state: "s1" })) auth.searchParams.set(k, v);

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage();
await page.goto(auth.toString(), { waitUntil: "networkidle0" });
const first = await page.evaluate(() => document.body.innerText.slice(0, 200));
if (await page.$("#username")) {
  await page.type("#username", "probe@gen9.test");
  await page.type("#password", "probe-user-pass-123");
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {}), page.click("#kc-login")]);
}
const consent = await page.evaluate(() => document.body.innerText.slice(0, 300));
if (await page.$('input[name="accept"], button[name="accept"]')) await page.click('input[name="accept"], button[name="accept"]');
const got = await Promise.race([code, new Promise((r) => setTimeout(() => r({ error: "timeout" }), 20000))]);
console.log("metadata fetched by Keycloak:", fetched, "time(s)");
console.log("first page:", first.replace(/\s+/g, " "));
console.log("consent page:", consent.replace(/\s+/g, " "));
console.log("callback:", { ...got, code: got.code ? "(a code)" : null });
if (got.code) {
  const token = await (await fetch(`${KC}/protocol/openid-connect/token`, { method: "POST", body: new URLSearchParams({ grant_type: "authorization_code", code: got.code, redirect_uri: REDIRECT, client_id: CLIENT_ID, code_verifier: verifier }) })).json();
  const claims = token.access_token ? JSON.parse(Buffer.from(token.access_token.split(".")[1], "base64url")) : token;
  console.log("token:", { aud: claims.aud, azp: claims.azp, scope: claims.scope, error: token.error, error_description: token.error_description });
}
await browser.close();
callback.close();
docs.close();
