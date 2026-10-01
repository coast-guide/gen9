// Temporal's web UI behind Keycloak, in real Chrome against the running stacks:
//   1. no anonymous access                  the UI sends you to Gen9's sign-in (client temporal-ui)
//   2. the seeded admin signs in            and sees Gen9's run workflows (gen9:admin)
//   3. the admin reads a run's input        decrypted by gen9-agent's codec endpoint, once TLS is in
//                                           front (the UI passes tokens only to https endpoints)
//   4. the seeded user signs in             is turned away at Keycloak in Gen9's words (no admin
//                                           role), and the UI still asks them to sign in
//   5. the codec endpoint                   refuses callers without an admin's temporal-ui token,
//                                           and allows only the UI's origin (CORS)
// Needs gen9-keycloak, gen9-temporal and gen9-agent up. It asks one short question of its own from
// the seeded admin's terminal, so there is a run to look at even on a fresh install, and deletes
// that chat (and so its workflow) at the end. Passwords are typed by Puppeteer from
// gen9-keycloak/.env, as in the other checks.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { tmpdir } from "node:os";
import { launch } from "./browser.mjs";
import { chatOf, deleteChats } from "./chats.mjs";
import { signInTerminal } from "./signin.mjs";
import { secondStep } from "./second-step.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const readEnv = (file) =>
  Object.fromEntries(
    (existsSync(`${ROOT}${file}`) ? readFileSync(`${ROOT}${file}`, "utf8") : "")
      .split("\n")
      .filter((line) => /^[A-Z0-9_]+=/.test(line))
      .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
  );
const env = readEnv("gen9-keycloak/.env");
const agentKeycloak = readEnv("gen9-agent/keycloak.local.env");
const UI = process.env.TEMPORAL_UI_URL ?? "http://localhost:18000";
const AGENT = process.env.AGENT_URL ?? "http://localhost:17000";
// Gen9 itself, where Keycloak's pages point back (gen9-keycloak's GEN9_UI_URL)
const APP = process.env.APP_URL ?? "http://localhost:14000";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

// A throwaway TLS proxy to gen9-agent, standing in for the TLS a real deployment puts in front
const HTTPS_PORT = 17443;
const certDir = mkdtempSync(`${tmpdir()}/gen9-codec-tls-`);
execFileSync("openssl", ["req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
  "-subj", "/CN=localhost", "-keyout", `${certDir}/key.pem`, "-out", `${certDir}/cert.pem`], { stdio: "ignore" });
const proxy = https.createServer(
  { key: readFileSync(`${certDir}/key.pem`), cert: readFileSync(`${certDir}/cert.pem`) },
  (request, response) => {
    const upstream = http.request(`${AGENT}${request.url}`, { method: request.method, headers: request.headers }, (reply) => {
      response.writeHead(reply.statusCode, reply.headers);
      reply.pipe(response);
    });
    upstream.on("error", () => response.writeHead(502).end());
    request.pipe(upstream);
  },
);
await new Promise((resolve) => proxy.listen(HTTPS_PORT, "127.0.0.1", resolve));
// A run of its own to look at: one short answer from the seeded admin's terminal
const adminCli = mkdtempSync(`${tmpdir()}/gen9-temporal-admin-`);
let ownChat = null;
if (await signInTerminal({ email: env.GEN9_SEED_ADMIN_EMAIL, password: env.GEN9_SEED_ADMIN_PASSWORD, configDir: adminCli })) {
  // The chat's id comes on the "Continue this chat: gen9 ask --thread …" line, on stderr
  const asked = spawnSync("uv", ["run", "-q", "gen9", "ask", "Reply with one word: pong"], {
    cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: adminCli }, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
  });
  ownChat = chatOf(`${asked.stdout}${asked.stderr}`);
}

const browser = await launch({
  acceptInsecureCerts: true,  // the proxy's self-signed certificate
  headless: !process.env.HEADED,
  defaultViewport: { width: 1400, height: 900 },
});

// Sign in to the UI in a fresh browser context; returns the page on the workflows list
async function signIn(email, password) {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  await page.goto(`${UI}/namespaces/gen9/workflows`, { waitUntil: "networkidle0" });
  const onLogin = page.url().startsWith(`${UI}/login`);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0" }),
    page.locator("button ::-p-text(Continue to SSO)").click(),
  ]);
  const onKeycloak = page.url().includes("/realms/gen9/protocol/openid-connect/auth") && page.url().includes("client_id=temporal-ui");
  await page.locator("#username").fill(email);
  await page.type("#password", password);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  await secondStep(page); // admins need a second step: the seeded admin's code
  // What Keycloak answered: a code for the UI, or its refusal (gen9-keycloak's gen9-temporal-ui flow)
  const answered = await page.evaluate(() => document.body.innerText);
  const heading = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => "");
  const wayBack = await page.$eval("#backToApplication", (a) => ({ text: a.textContent.trim(), href: a.href })).catch(() => null);
  await page.goto(`${UI}/namespaces/gen9/workflows`, { waitUntil: "networkidle0" });
  return { page, context, onLogin, onKeycloak, answered, heading, wayBack };
}

const bodyText = (page) => page.evaluate(() => document.body.innerText);

try {
  // 1-2: the admin
  const admin = await signIn(env.GEN9_SEED_ADMIN_EMAIL, env.GEN9_SEED_ADMIN_PASSWORD);
  check(admin.onLogin && admin.onKeycloak, "no anonymous access: the UI sends you to Gen9's sign-in (client temporal-ui)");
  await admin.page.waitForFunction(() => document.body.innerText.includes("RunWorkflow"), { timeout: 20_000 }).catch(() => {});
  const listed = await bodyText(admin.page);
  check(listed.includes("RunWorkflow"), "the admin sees Gen9's run workflows", listed.includes("RunWorkflow") ? "" : listed.slice(0, 200));

  // 3: decrypted payloads for the admin. Temporal's UI passes the user's token only to an https
  // codec endpoint (validateHttps in its data-encoder.ts), so over plain http it shows ciphertext.
  // With TLS in front (here a throwaway https proxy to gen9-agent, set through the UI's own
  // per-browser codec override) it shows the run's input decrypted.
  const runLink = await admin.page.$$eval("a[href*='/workflows/run-']", (links) => links.map((a) => a.href)[0]);
  if (check(!!runLink, "a run workflow to open", runLink ?? "none listed")) {
    const history = runLink.replace(/\/timeline$/, "/history");
    const inputShown = async () => {
      // A run still waiting for its person keeps the UI polling, so the network never idles:
      // wait for the input itself, shown decrypted or not
      await admin.page.goto(history, { waitUntil: "load" });
      await admin.page
        .waitForFunction(() => /user_sub|binary\/encrypted/.test(document.body.innerText), { timeout: 20_000 })
        .catch(() => {});
      await admin.page.waitForFunction(() => document.body.innerText.includes("user_sub"), { timeout: 5_000 }).catch(() => {});
      const text = await bodyText(admin.page);
      return { decrypted: text.includes("user_sub"), encrypted: text.includes("binary/encrypted") };
    };
    const plain = await inputShown();
    check(!plain.decrypted, "over http the UI shows the run's input as ciphertext (tokens go only to https codec endpoints)");
    await admin.page.evaluate((endpoint) => {
      localStorage.setItem("endpoint", JSON.stringify(endpoint));
      localStorage.setItem("passAccessToken", "true");
      localStorage.setItem("overrideRemoteCodecConfiguration", "true");
    }, `https://localhost:${HTTPS_PORT}/v1/temporal/codec`);
    const shown = await inputShown();
    check(shown.decrypted && !shown.encrypted, "over https the admin reads the run's input decrypted by gen9-agent's codec endpoint");
  }
  await admin.context.close();

  // 4: a regular user, turned away at Keycloak in Gen9's words: before, they got in without a
  // Temporal role and the UI sent them back to its sign-in page, over and over
  const user = await signIn(env.GEN9_SEED_USER_EMAIL, env.GEN9_SEED_USER_PASSWORD);
  check(
    user.answered.includes("Temporal is for Gen9 admins."),
    "a regular user is turned away at Keycloak, in Gen9's words",
    user.answered.replace(/\s+/g, " ").slice(0, 160),
  );
  // Under its own heading, with a way back to Gen9 (P3-D6): before, "Something went wrong" and no link
  check(
    user.heading === "Temporal is for admins" && user.wayBack?.text === "Back to Gen9" && user.wayBack.href === `${APP}/chat`,
    "the refusal has its own heading and a way back to Gen9",
    `${user.heading}; ${user.wayBack ? `${user.wayBack.text} → ${user.wayBack.href}` : "no link"}`,
  );
  check(user.page.url().startsWith(`${UI}/login`), "no session in the UI for them: it asks to sign in", user.page.url().split("?")[0]);
  await user.context.close();

  // 5: the codec endpoint on its own
  const body = JSON.stringify({ payloads: [] });
  const bare = await fetch(`${AGENT}/v1/temporal/codec/decode`, { method: "POST", body, headers: { "content-type": "application/json" } });
  check(bare.status === 401, "the codec endpoint refuses a call without a token", `HTTP ${bare.status}`);
  const service = await fetch(`${agentKeycloak.KEYCLOAK_ISSUER}/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "client_credentials" }),
    headers: { authorization: `Basic ${Buffer.from(`${agentKeycloak.KEYCLOAK_ADMIN_CLIENT_ID}:${agentKeycloak.KEYCLOAK_ADMIN_CLIENT_SECRET}`).toString("base64")}` },
  }).then((r) => r.json());
  const otherClient = await fetch(`${AGENT}/v1/temporal/codec/decode`, {
    method: "POST",
    body,
    headers: { "content-type": "application/json", authorization: `Bearer ${service.access_token}` },
  });
  check(otherClient.status === 401, "…and a valid Keycloak token from another client (gen9-agent's own)", `HTTP ${otherClient.status}`);
  const preflight = (origin) =>
    fetch(`${AGENT}/v1/temporal/codec/decode`, {
      method: "OPTIONS",
      headers: { origin, "access-control-request-method": "POST", "access-control-request-headers": "authorization,content-type,x-namespace" },
    }).then((r) => r.headers.get("access-control-allow-origin"));
  const [ours, theirs] = [await preflight(UI), await preflight("https://example.com")];
  check(ours === UI && theirs === null, "CORS allows only the UI's origin", `${UI} -> ${ours}, example.com -> ${theirs}`);
} finally {
  await browser.close();
  proxy.close();
  rmSync(certDir, { recursive: true, force: true });
  // Its own chat goes, and with it the run's workflow
  const left = await deleteChats(adminCli, [ownChat]).catch(() => [ownChat]);
  check(ownChat && !left.length, "its own chat is deleted", ownChat ?? "no chat was made");
  execFileSync("uv", ["run", "-q", "gen9", "logout"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: adminCli }, stdio: "ignore" });
  rmSync(adminCli, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
