// Another site can't read or change a signed-in person's Gen9 (manual-e2e.md, P3-F1): gen9-ui is
// the only browser client, gen9-agent allows no origin but Temporal's web UI on its codec
// endpoint, and the session cookie is SameSite=Lax.
//   1. headers: to a foreign Origin, no Access-Control-Allow-Origin or -Credentials from the web
//      app or the API; the codec endpoint answers Temporal's UI and refuses the foreign origin
//   2. in Chrome, signed in as the seeded user: the app reads their chats; a page on another site
//      (127.0.0.1 is not localhost's site) fetching the app and the API with credentials is
//      blocked every time, and Chrome withholds the session cookie from each request (its own
//      verdict, over CDP)
//   3. a chat's HTML and SVG files, whose scripts would mark Gen9's origin, download when opened
//      (attachment, nosniff, a sandbox CSP) and never run: not opened, nor the SVG as an image
//   4. the CSP reports what it blocks (report-uri; report-to too over https): the app's screens,
//      the sign-in error page among them, have it and trip nothing, an injected image is
//      reported, and gen9-ui logs it without its query
// It deletes the chat it makes. No model call.
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromeOnly, FIREFOX, launch } from "./browser.mjs";
import { signInTerminal } from "./signin.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const API = process.env.GEN9_API ?? "http://localhost:17000";
const TEMPORAL_UI = process.env.TEMPORAL_UI_URL ?? "http://localhost:18000";
const OTHER_PORT = Number(process.env.OTHER_SITE_PORT ?? 17996);
const FOREIGN = "https://evil.example";

let failures = 0;
function check(ok, what, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
}
const cors = (response) => [response.headers.get("access-control-allow-origin"), response.headers.get("access-control-allow-credentials")];

// 1. Headers
const answers = [];
for (const [method, url] of [
  ["OPTIONS", `${API}/v1/threads`],
  ["GET", `${API}/v1/me`],
  ["OPTIONS", `${APP}/api/threads`],
  ["GET", `${APP}/api/threads`],
  ["GET", `${APP}/api/export`],
]) {
  const response = await fetch(url, { method, headers: { Origin: FOREIGN, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization,content-type" } });
  answers.push({ what: `${method} ${url.replace(/^http:\/\/localhost:/, ":")} ${response.status}`, cors: cors(response) });
}
check(answers.every((a) => !a.cors[0] && !a.cors[1]), "to a foreign origin, neither the app nor the API allows it or credentials", answers.map((a) => a.what).join(", "));
const codec = async (origin) =>
  fetch(`${API}/v1/temporal/codec/decode`, { method: "OPTIONS", headers: { Origin: origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type" } });
const ui = await codec(TEMPORAL_UI);
const foreign = await codec(FOREIGN);
check(cors(ui)[0] === TEMPORAL_UI && !cors(ui)[1] && !cors(foreign)[0], "the codec endpoint answers Temporal's UI (no credentials) and not a foreign origin", `${ui.status} ${cors(ui)[0]}; ${foreign.status}`);

// 2. In Chrome
const other = createServer((_, res) => {
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end("<!doctype html><title>Another site</title>");
});
await new Promise((resolve) => other.listen(OTHER_PORT, "127.0.0.1", resolve));
// Firefox saves downloads here by its own settings (it has no DevTools protocol to watch them)
const saved = mkdtempSync(join(tmpdir(), "gen9-cross-site-downloads-"));
const browser = await launch({
  headless: !process.env.HEADED,
  extraPrefsFirefox: { "browser.download.folderList": 2, "browser.download.dir": saved, "browser.download.useDownloadDir": true, "browser.download.always_ask_before_handling_new_types": false },
});
let cdp;
let thread, deleteThread;
try {
  const page = await browser.newPage();
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_USER_EMAIL);
  await page.type("#password", env.GEN9_SEED_USER_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  // Chrome's own account of each request's session cookie: sent, or withheld and why (Firefox has
  // no DevTools protocol: there the fetches' outcomes alone)
  const verdicts = [];
  if (!FIREFOX) {
    cdp = await page.createCDPSession();
    await cdp.send("Network.enable");
    cdp.on("Network.requestWillBeSentExtraInfo", (e) => {
      const session = (e.associatedCookies ?? []).find((c) => c.cookie.name.includes("gen9_session"));
      if (session) verdicts.push(session.blockedReasons.length ? `withheld (${session.blockedReasons.join(",")})` : "sent");
    });
  }
  const own = await page.evaluate(async () => (await fetch("/api/threads")).status);
  await new Promise((resolve) => setTimeout(resolve, 300));
  check(own === 200 && (FIREFOX || verdicts[0] === "sent"), "signed in, the app reads their chats, the cookie sent", `${own}, ${verdicts[0] ?? "(the fetch's status)"}`);

  verdicts.length = 0;
  await page.goto(`http://127.0.0.1:${OTHER_PORT}/`, { waitUntil: "networkidle0" });
  const tries = await page.evaluate(
    async (app, api) => {
      const attempt = async (url, init) => {
        try {
          const r = await fetch(url, { credentials: "include", ...init });
          return `read ${r.status}`;
        } catch (e) {
          return `blocked (${e.name})`;
        }
      };
      return [
        await attempt(`${app}/api/threads`),
        await attempt(`${app}/api/export`),
        await attempt(`${app}/api/threads`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }),
        await attempt(`${api}/v1/me`),
      ];
    },
    APP,
    API,
  );
  await new Promise((resolve) => setTimeout(resolve, 500));
  check(tries.every((t) => t.startsWith("blocked")), "from another site, every fetch with credentials is blocked (the chats, the export, a new chat, the API)", tries.join(", "));
  if (!chromeOnly("and Chrome withholds the session cookie from each request")) {
    check(verdicts.length > 0 && verdicts.every((v) => v.startsWith("withheld")), "and Chrome withholds the session cookie from each request", verdicts.join(", "));
  }

  // 3. Files a browser would run, attached to a chat of theirs (through the API, with a token)
  const config = mkdtempSync(join(tmpdir(), "gen9-cross-site-"));
  try {
    await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: config });
    const auth = { Authorization: `Bearer ${JSON.parse(readFileSync(join(config, "credentials.json"), "utf8")).access_token}` };
    thread = (await (await fetch(`${API}/v1/threads`, { method: "POST", headers: auth })).json()).id;
    deleteThread = () => fetch(`${API}/v1/threads/${thread}`, { method: "DELETE", headers: auth });
    const mark = "localStorage.setItem('ran', location.href)";
    const attach = async (name, body) =>
      (await fetch(`${API}/v1/threads/${thread}/files?name=${name}`, { method: "POST", headers: { ...auth, "Content-Type": "application/octet-stream" }, body })).json();
    const files = [
      await attach("page.html", `<!doctype html><title>page</title><script>${mark}</script>`),
      await attach("drawing.svg", `<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>${mark}</script><rect width="10" height="10"/></svg>`),
    ];
    let downloads = [];
    if (!FIREFOX) {
      await cdp.send("Browser.setDownloadBehavior", { behavior: "deny", eventsEnabled: true });
      cdp.on("Browser.downloadWillBegin", (e) => downloads.push(e.suggestedFilename));
    }
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    await page.evaluate(() => localStorage.removeItem("ran"));
    const heads = [];
    for (const file of files) {
      const url = `${APP}/api/threads/${thread}/files/${file.id}`;
      heads.push(await page.evaluate(async (u) => { const r = await fetch(u); return [r.headers.get("content-disposition")?.split(";")[0], r.headers.get("x-content-type-options"), r.headers.get("content-security-policy")].join(" "); }, url));
      await page.goto(url).catch(() => {}); // a download: the navigation is aborted
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    if (FIREFOX) downloads = readdirSync(saved).filter((f) => !f.endsWith(".part"));
    const stayed = page.url() === `${APP}/chat`;
    const asImage = await page.evaluate(async (u) => {
      const img = new Image();
      const loaded = await new Promise((resolve) => { img.onload = () => resolve("shown"); img.onerror = () => resolve("not shown"); img.src = u; document.body.append(img); });
      await new Promise((resolve) => setTimeout(resolve, 400));
      return loaded;
    }, `${APP}/api/threads/${thread}/files/${files[1].id}`);
    const ran = await page.evaluate(() => localStorage.getItem("ran"));
    check(
      heads.every((h) => h === "attachment nosniff sandbox") && downloads.length === 2 && stayed && ran === null,
      "a chat's HTML and SVG files download when opened, and their scripts never run as Gen9 (nor the SVG as an image)",
      `${heads.join(" | ")}; downloads: ${downloads.join(", ")}; SVG as an image: ${asImage}; a script ran: ${ran ?? "no"}`,
    );
  } finally {
    rmSync(config, { recursive: true, force: true });
  }

  // 4. The CSP's reports: none from the app's own screens; a blocked image reported and logged
  const reports = [];
  page.on("request", (r) => {
    if (!r.url().includes("/api/csp-report")) return;
    // Firefox's requests don't show their bodies over WebDriver BiDi: counted there, and the
    // server's log line below says what the report held
    if (FIREFOX) return reports.push("a report (its body unread in Firefox)");
    const b = JSON.parse(r.postData() ?? "{}")["csp-report"] ?? {};
    reports.push(`${b["effective-directive"]} ${b["blocked-uri"]} (${new URL(b["document-uri"] ?? APP).pathname})`);
  });
  const policies = [];
  for (const path of ["/chat", "/search?q=reply", "/scheduled", "/settings", "/auth/error?reason=state"]) {
    const response = await page.goto(`${APP}${path}`, { waitUntil: "networkidle0" });
    policies.push(`${path.split("?")[0]} ${response.headers()["content-security-policy"] ? "has a CSP" : "has no CSP"}`);
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
  check(reports.length === 0, "the app's screens trip nothing in the CSP (chat, search, scheduled, settings, the sign-in error page)", reports.join(", ") || "no reports");
  // P7-E2: the sign-in error page is a page among route handlers, and was served with no CSP
  check(policies.every((p) => p.endsWith("has a CSP")), "every screen has the CSP, the sign-in error page too", policies.join("; "));
  const since = new Date().toISOString();
  const tag = `card-${Date.now()}`;
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await page.evaluate((t) => { const img = new Image(); img.src = `https://httpbin.org/image/png?${t}`; document.body.append(img); }, tag);
  let logged = "";
  for (let i = 0; i < 20 && !logged.includes("[csp] img-src"); i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    logged = execFileSync("sh", ["-c", `docker logs --since ${since} gen9-ui-prod-1 2>&1`], { encoding: "utf8" });
  }
  const line = logged.split("\n").find((l) => l.includes("[csp] img-src")) ?? "";
  check(
    (FIREFOX ? reports.length > 0 : reports.some((r) => r.startsWith("img-src https://httpbin.org"))) && line.includes("blocked https://httpbin.org/image/png on /chat") && !logged.includes(tag),
    "an injected image is blocked, reported, and logged without its query",
    line.replace(/^.*\[csp\]/, "[csp]"),
  );
} catch (e) {
  check(false, "the cross-site check ran to the end", e.message);
} finally {
  await browser.close();
  rmSync(saved, { recursive: true, force: true });
  other.close();
  if (deleteThread) await deleteThread();
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
