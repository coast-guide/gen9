// Forgot password can't get past an authenticator app, in real Chrome:
//   1. a throwaway user (Admin API) signs in and sets up an authenticator app
//   2. sign out, "Forgot password?", the reset email's link (read from Mailpit)
//   3. the link asks for the authenticator code (Keycloak's built-in flow instead asks to set up a
//      new authenticator, so the email alone was enough); a wrong code is refused
//   4. the right code, then a new password: signed in, still with the one authenticator app
//   5. a person with no second step: the link leads straight to a new password, then signed in
// gen9-keycloak/config/configure.sh builds and binds the flow (gen9-reset-credentials). The user is
// deleted at the end, whatever happens.
import { createHmac, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
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
const MAILPIT = process.env.MAILPIT_URL ?? "http://localhost:15002";
// Mailpit's API asks for its password (user gen9; MAILPIT_UI_PASSWORD in gen9-keycloak/.env)
const MAIL = { headers: { Authorization: `Basic ${Buffer.from(`gen9:${env.MAILPIT_UI_PASSWORD ?? ""}`).toString("base64")}` } };
const EMAIL = `recovery-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const NEW_PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

// Keycloak Admin API as the bootstrap admin: the throwaway user, its credentials and events
async function admin(path, init = {}) {
  // admin-cli's tokens live 60 s: a new one after 50 s, or a cleanup at the end of a long run
  // fails silently and leaves its user behind (as gen9-learn's lib.mjs does)
  if (Date.now() - (admin.at ?? 0) > 50_000) {
    admin.token = null;
    admin.at = Date.now();
  }
  admin.token ??= await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "admin-cli",
      username: env.KC_BOOTSTRAP_ADMIN_USERNAME,
      password: env.KC_BOOTSTRAP_ADMIN_PASSWORD,
    }),
  })
    .then((r) => r.json())
    .then((body) => body.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${admin.token}`, "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 201 || response.status === 204 ? null : response.json();
}

// RFC 6238 with the realm's policy; Keycloak keys the HMAC with the secret's raw bytes
const realm = await admin("");
const period = realm.otpPolicyPeriod;
const totp = (secret, counter) => {
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const algorithm = realm.otpPolicyAlgorithm.replace("Hmac", "").toLowerCase();
  const hash = createHmac(algorithm, Buffer.from(secret, "utf8")).update(message).digest();
  const offset = hash[hash.length - 1] & 0xf;
  const value = (hash.readUInt32BE(offset) & 0x7fffffff) % 10 ** realm.otpPolicyDigits;
  return String(value).padStart(realm.otpPolicyDigits, "0");
};
const counterNow = () => Math.floor(Date.now() / 1000 / period);

await admin("/users", {
  method: "POST",
  body: JSON.stringify({
    username: EMAIL,
    email: EMAIL,
    firstName: "Recovery",
    lastName: "Check",
    enabled: true,
    emailVerified: true,
    requiredActions: ["CONFIGURE_TOTP"],
    credentials: [{ type: "password", value: PASSWORD, temporary: false }],
  }),
});
const [user] = await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`);
let plainUser; // the second throwaway user, with no second step (step 5)
const startedAt = Date.now();

const browser = await launch({
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
});
const page = await browser.newPage();
const navigation = (action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
const inApp = () => page.url().startsWith(`${APP}/`) && !page.url().includes("/signed-out");
const otpCredentials = async () => (await admin(`/users/${user.id}/credentials`)).filter((c) => c.type === "otp");

try {
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", EMAIL);
  await page.type("#password", PASSWORD);
  await navigation(page.click("#kc-login"));
  const secret = await page.$eval("#totpSecret", (input) => input.value).catch(() => null);
  if (!check(!!secret, "after the password, Keycloak asks to set up an authenticator app", page.url())) throw new Error("cannot continue");
  const setupCounter = counterNow();
  await page.type("#totp", totp(secret, setupCounter));
  await navigation(page.click("#saveTOTPBtn"));
  if (!check(inApp(), "authenticator app set up, signed in", page.url())) throw new Error("cannot continue");
  check((await otpCredentials()).length === 1, "Keycloak holds one authenticator app for the user");

  await navigation(page.evaluate(() => document.querySelector('form[action="/auth/logout"]').requestSubmit()));
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await navigation(page.click('a[href*="reset-credentials"]'));
  await page.type("#username", EMAIL);
  await navigation(page.click('#kc-reset-password-form [type="submit"]'));

  // The reset email, in Mailpit
  let link;
  for (let i = 0; i < 30 && !link; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const found = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${EMAIL}"`)}`, MAIL).then((r) => r.json());
    const message = found.messages?.find((m) => Date.parse(m.Created) >= startedAt - 5000);
    if (message) {
      const { Text } = await fetch(`${MAILPIT}/api/v1/message/${message.ID}`, MAIL).then((r) => r.json());
      link = Text.match(/https?:\/\/\S+\/login-actions\/action-token\?\S+/)?.[0];
    }
  }
  if (!check(!!link, "the reset email arrived with its link")) throw new Error("cannot continue");

  await page.goto(link, { waitUntil: "networkidle0" });
  const asksForCode = !!(await page.$("#otp"));
  check(asksForCode, "the link asks for the authenticator code, not for a new authenticator", page.url());
  check(!(await page.$("#totpSecret")), "no page to set up another authenticator");
  if (!asksForCode) throw new Error("cannot continue");

  await page.type("#otp", "000000" === totp(secret, counterNow()) ? "111111" : "000000");
  await navigation(page.click("#kc-login"));
  check(!!(await page.$("#otp")) && !(await page.$("#password-new")), "a wrong code is refused");

  // Keycloak won't take the code used at setup again: wait for the next one
  while (counterNow() <= setupCounter) await new Promise((resolve) => setTimeout(resolve, 500));
  await page.$eval("#otp", (input) => (input.value = ""));
  await page.type("#otp", totp(secret, counterNow()));
  await navigation(page.click("#kc-login"));
  if (!check(!!(await page.$("#password-new")), "the right code leads to choosing a new password", page.url())) throw new Error("cannot continue");

  await page.type("#password-new", NEW_PASSWORD);
  await page.type("#password-confirm", NEW_PASSWORD);
  await navigation(page.click('#kc-passwd-update-form [type="submit"]'));
  check(inApp(), "new password set, signed in", page.url());

  const otp = await otpCredentials();
  check(otp.length === 1, "still exactly one authenticator app (none added or replaced)", `${otp.length}`);
  const events = await admin(`/events?user=${user.id}&max=50`);
  check(events.filter((e) => e.type === "UPDATE_TOTP").length === 1, "Keycloak logged one authenticator setup, none during the reset");
  check(events.some((e) => e.type === "UPDATE_PASSWORD"), "Keycloak logged the password change");

  // 5. A person with no second step: the email alone resets the password (M9, item 7)
  const plain = { email: `recovery-plain-${Date.now()}@gen9.test`, password: `e2e-${randomBytes(12).toString("hex")}`, next: `e2e-${randomBytes(12).toString("hex")}` };
  await admin("/users", { method: "POST", body: JSON.stringify({ username: plain.email, email: plain.email, firstName: "Recovery", lastName: "Plain", enabled: true, emailVerified: true, credentials: [{ type: "password", value: plain.password, temporary: false }] }) });
  [plainUser] = await admin(`/users?email=${encodeURIComponent(plain.email)}&exact=true`);
  const other = await (await browser.createBrowserContext()).newPage();
  const go = (action) => Promise.all([other.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
  const plainSince = Date.now();
  await other.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await go(other.click('a[href*="reset-credentials"]'));
  await other.type("#username", plain.email);
  await go(other.click('#kc-reset-password-form [type="submit"]'));
  let plainLink;
  for (let i = 0; i < 30 && !plainLink; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const found = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${plain.email}"`)}`, MAIL).then((r) => r.json());
    const message = found.messages?.find((m) => Date.parse(m.Created) >= plainSince - 5000);
    if (message) {
      const { Text } = await fetch(`${MAILPIT}/api/v1/message/${message.ID}`, MAIL).then((r) => r.json());
      plainLink = Text.match(/https?:\/\/\S+\/login-actions\/action-token\?\S+/)?.[0];
    }
  }
  if (!check(!!plainLink, "no second step: the reset email arrived with its link")) throw new Error("cannot continue");
  await other.goto(plainLink, { waitUntil: "networkidle0" });
  const straight = !!(await other.$("#password-new")) && !(await other.$("#otp"));
  check(straight, "no second step: the link leads straight to choosing a new password", other.url().replace(/\?.*/, ""));
  if (straight) {
    await other.type("#password-new", plain.next);
    await other.type("#password-confirm", plain.next);
    await go(other.click('#kc-passwd-update-form [type="submit"]'));
    const plainEvents = await admin(`/events?user=${plainUser.id}&max=20`);
    check(other.url().startsWith(`${APP}/`) && !other.url().includes("/signed-out") && plainEvents.some((e) => e.type === "UPDATE_PASSWORD"),
      "no second step: new password set, signed in, and Keycloak logged it", other.url().replace(/\?.*/, ""));
  }
} catch (error) {
  check(false, "unexpected error", error.message);
} finally {
  await browser.close();
  if (plainUser) await admin(`/users/${plainUser.id}`, { method: "DELETE" }).catch((error) => check(false, "delete the second throwaway user", error.message));
  await admin(`/users/${user.id}`, { method: "DELETE" }).then(
    () => console.log("      (throwaway user deleted)"),
    (error) => check(false, "delete the throwaway user", error.message),
  );
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
