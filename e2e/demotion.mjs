// An admin whose access is removed loses the admin links at once (manual-e2e.md, P3-D7). A
// session's roles come from its token, which kept gen9-admin until it expired, while gen9-agent,
// which asks Keycloak, refused at once. As a throwaway person made an admin through Keycloak's
// "admins" group, in Chrome:
//   1. an admin sees Users and the three admin links
//   2. removed from the group while reading Users, then opening the audit log from the sidebar:
//      "You need admin access", and no admin links, then or after a reload
//   3. made an admin again and signed in again, with "Make … an admin?" open; removed, then
//      confirming: "You need admin access.", the links gone in that response, nobody promoted
// Admins need a second step (gen9-keycloak/config/configure.sh): as an admin with none, their first
// sign-in sets up an authenticator app, and the next asks for its code.
// It deletes what it made, and costs no model call.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { launch } from "./browser.mjs";
import { secondStep } from "./second-step.mjs";
import { forget } from "./forget.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const EMAIL = `demotion-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const TARGET = `demotion-target-${Date.now()}@gen9.test`;

let failures = 0;
function check(ok, what, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
}
async function admin(method, path, body) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${method} ${path}: ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
const userId = async (email) => (await admin("GET", `/users?email=${encodeURIComponent(email)}&exact=true`))[0]?.id;

await admin("POST", "/users", { username: EMAIL, email: EMAIL, firstName: "Demoted", lastName: "Admin", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] });
await admin("POST", "/users", { username: TARGET, email: TARGET, firstName: "Not", lastName: "Promoted", enabled: true, emailVerified: true });
const id = await userId(EMAIL);
const targetId = await userId(TARGET);
const [group] = (await admin("GET", "/groups?search=admins&exact=true")).filter((g) => g.name === "admins");
const grant = () => admin("PUT", `/users/${id}/groups/${group.id}`);
const revoke = () => admin("DELETE", `/users/${id}/groups/${group.id}`);

const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
const adminLinks = (page) => page.$$eval('a[href^="/admin/"]', (as) => as.map((a) => a.getAttribute("href")));
// Admins need a second step: the person sets up an authenticator app at their first sign-in as
// one, and answers its code after (second-step.mjs)
let otpSecret;
async function signIn() {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  await page.goto(`${APP}/auth/login?returnTo=/admin/users`, { waitUntil: "networkidle0" });
  await page.type("#username", EMAIL);
  await page.type("#password", PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  const second = await secondStep(page, otpSecret);
  if (second?.did === "set up") otpSecret = second.secret;
  return { context, page, second };
}
try {
  // 1-2. Removed while reading Users; then the audit log, from the sidebar
  await grant();
  let { context, page, second } = await signIn();
  check(second?.did === "set up", "an admin with no second step sets up an authenticator app at that sign-in", JSON.stringify(second?.did ?? null));
  const links = await adminLinks(page);
  check(links.length === 3 && (await page.$eval("h1", (h) => h.textContent.trim())) === "Users", "an admin sees Users and the three admin links", links.join(", "));
  await revoke();
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {}), page.click('a[href="/admin/audit"]')]);
  await page.waitForFunction(() => !document.querySelector('a[href^="/admin/"]'), { timeout: 10_000 }).catch(() => {});
  const heading = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => "");
  const after = await adminLinks(page);
  check(heading === "You need admin access" && after.length === 0, "removed, an admin page says so and the admin links go at once", `${heading}; links: ${after.join(", ") || "none"}`);
  await page.reload({ waitUntil: "networkidle0" });
  check((await adminLinks(page)).length === 0, "and stay gone after a reload");
  await context.close();

  // 3. Removed with "Make … an admin?" open; then confirmed
  await grant();
  ({ context, page, second } = await signIn());
  check(second?.did === "code", "signed in again, the admin is asked for their authenticator code", JSON.stringify(second?.did ?? null));
  await page.goto(`${APP}/admin/users?q=${encodeURIComponent(TARGET)}`, { waitUntil: "networkidle0" });
  await page.click(`button[aria-label="Actions for ${TARGET}"]`);
  await page.waitForSelector("[role=menuitem]");
  for (const item of await page.$$("[role=menuitem]")) if ((await item.evaluate((e) => e.textContent.trim())) === "Make admin…") await item.click();
  await page.waitForSelector('[role="alertdialog"]');
  await revoke();
  for (const button of await page.$$('[role="alertdialog"] button')) if ((await button.evaluate((e) => e.textContent.trim())) === "Make admin") await button.click();
  const toast = await page
    .waitForFunction(() => document.querySelector("[data-sonner-toast]")?.textContent.trim() || false, { timeout: 10_000 })
    .then((h) => h.jsonValue())
    .catch(() => null);
  await page.waitForFunction(() => !document.querySelector('a[href^="/admin/"]'), { timeout: 5_000 }).catch(() => {});
  const left = await adminLinks(page);
  const promoted = (await admin("GET", `/users/${targetId}/groups`)).some((g) => g.name === "admins");
  check(toast === "You need admin access." && left.length === 0 && !promoted, "confirmed after removal: refused, the links gone in the same response, nobody promoted", `${toast}; links: ${left.join(", ") || "none"}`);
  await context.close();
} catch (e) {
  check(false, "the demotion check ran to the end", e.message);
} finally {
  await browser.close();
  // Deleted in Keycloak, then their Gen9 data at once (forget.mjs)
  for (const user of [id, targetId]) if (user) await admin("DELETE", `/users/${user}`).catch(() => {});
  forget(id, targetId);
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
