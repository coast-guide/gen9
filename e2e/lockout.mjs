// A locked account signs nothing in, in real Chrome and on the terminal (Keycloak's brute-force
// protection: the realm locks an account after 5 wrong passwords):
//   1. a throwaway person (Admin API) signs in to the web app
//   2. 5 wrong passwords in another browser: the right one is refused there too, and Keycloak
//      holds the account locked
//   3. `gen9 login` (the device grant), its code confirmed in the first browser, which still has
//      the person's session: the page says the device is signed in, and the terminal gets no
//      token. Keycloak 26.7.4 gave it one (CVE-2026-88770, fixed in 26.7.5)
//   4. the lockout cleared (as an admin's Unlock sign-in does): the same confirmation signs the
//      terminal in
// No model call. The person is deleted at the end, whatever happens.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch } from "./browser.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const EMAIL = `lockout-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
async function admin(method, path, body) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok) throw new Error(`Keycloak ${method} ${path}: ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
const submitted = (page) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);

// `gen9 login` with its code confirmed in `page`: what the page says, and gen9 login's last line
function terminalLogin(page, configDir) {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", "login"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    let said = "";
    let confirming = false;
    const timer = setTimeout(() => child.kill(), 90_000);
    const onData = async (data) => {
      out += data;
      const url = out.match(/http\S+user_code=\S+/)?.[0];
      if (!url || confirming) return;
      confirming = true;
      await page.goto(url, { waitUntil: "networkidle0" });
      for (let step = 0; step < 4; step++) {
        const next = page.waitForNavigation({ waitUntil: "networkidle0", timeout: 15_000 }).catch(() => {});
        if (await page.$('button[name="accept"]')) await page.click('button[name="accept"]');
        else if (await page.$("#kc-login")) await page.click("#kc-login");
        else break;
        await next;
      }
      said = await page.evaluate(() => document.querySelector("h1")?.textContent.trim() ?? "");
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("exit", () => {
      clearTimeout(timer);
      resolve({ said, answer: out.trim().split("\n").filter((l) => /Signed in as|credentials|denied|expired|error/i.test(l)).at(-1) ?? "(no answer)" });
    });
  });
}

await admin("POST", "/users", { username: EMAIL, email: EMAIL, firstName: "Locked", lastName: "Out", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] });
const id = (await admin("GET", `/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
const dirs = [];
const browser = await launch({ headless: !process.env.HEADED });
try {
  // 1. Signed in to the web app
  const web = await (await browser.createBrowserContext()).newPage();
  await web.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
  await web.type("#username", EMAIL);
  await web.type("#password", PASSWORD);
  await submitted(web);
  check(web.url().startsWith(`${APP}/chat`), "a throwaway person signs in to the web app", web.url());

  // 2. Locked by 5 wrong passwords in another browser
  const other = await (await browser.createBrowserContext()).newPage();
  for (let i = 0; i < 6; i++) {
    await other.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
    await other.type("#username", EMAIL);
    await other.type("#password", i < 5 ? `wrong-${i}-password-xx` : PASSWORD);
    await submitted(other);
  }
  const locked = await admin("GET", `/attack-detection/brute-force/users/${id}`);
  check(!other.url().startsWith(APP) && locked.disabled === true && locked.numFailures >= 5, "after 5 wrong passwords the right one is refused, and Keycloak holds the account locked", `failures ${locked.numFailures}, locked ${locked.disabled}`);

  // 3. The terminal, confirmed from the browser that still has the session
  dirs.push(mkdtempSync(join(tmpdir(), "gen9-lockout-")));
  const refused = await terminalLogin(web, dirs[0]);
  check(
    !/Signed in as/.test(refused.answer) && !existsSync(join(dirs[0], "credentials.json")),
    "locked, the terminal gets no token, even confirmed from a browser still signed in (CVE-2026-88770)",
    `page: "${refused.said}"; gen9 login: ${refused.answer}`,
  );

  // 4. Unlocked, the same confirmation signs the terminal in
  await admin("DELETE", `/attack-detection/brute-force/users/${id}`);
  dirs.push(mkdtempSync(join(tmpdir(), "gen9-lockout-")));
  const unlocked = await terminalLogin(web, dirs[1]);
  check(/Signed in as/.test(unlocked.answer) && existsSync(join(dirs[1], "credentials.json")), "unlocked, the same confirmation signs the terminal in", unlocked.answer);
} catch (error) {
  check(false, "the lockout check ran to the end", error.message.split("\n")[0]);
} finally {
  await browser.close();
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
  // gen9-agent's sweep removes their Gen9 data once they're gone from Keycloak
  await admin("DELETE", `/users/${id}`).catch(() => {});
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall lockout checks passed");
process.exit(failures ? 1 : 0);
