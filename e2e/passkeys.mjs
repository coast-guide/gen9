// Passkeys end to end in real Chrome, across gen9-ui and gen9-keycloak:
//   1. sign in with a password (the seed admin from gen9-keycloak/.env)
//   2. Settings → Add a passkey → Gen9's "Add a passkey" page → created without a browser dialog
//   3. sign out → signed in again by passkey autofill, no password
//   4. sign out → signed in with the "Sign in with a passkey" button (a browser without autofill)
//   5. Settings lists the passkey by name → Remove → Keycloak asks to confirm → removed
// Chrome's virtual authenticator (DevTools WebAuthn domain) stands in for Touch ID or Windows Hello:
// a platform authenticator with discoverable credentials and user verification. Keycloak's event
// log must show both passkey sign-ins and the removal. If a step fails, the passkey created is
// deleted through the Admin API instead.
import { readFileSync } from "node:fs";
import { chromeOnly, launch } from "./browser.mjs";
import { secondStep } from "./second-step.mjs";

if (chromeOnly("passkeys", "Chrome's virtual authenticator is a DevTools domain; Firefox has no DevTools protocol")) process.exit(0);

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const EMAIL = env.GEN9_SEED_ADMIN_EMAIL;
const PASSWORD = env.GEN9_SEED_ADMIN_PASSWORD;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

// Keycloak Admin API as the bootstrap admin, for credentials and events only
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
    headers: { Authorization: `Bearer ${admin.token}` },
  });
  if (!response.ok) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 204 ? null : response.json();
}
const passkeys = async (userId) =>
  (await admin(`/users/${userId}/credentials`)).filter((c) => c.type === "webauthn-passwordless");

const [user] = await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`);
const before = new Set((await passkeys(user.id)).map((c) => c.id));
const startedAt = Date.now();

const browser = await launch({
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
});
const page = await browser.newPage();
const dialogs = [];
page.on("dialog", async (dialog) => {
  dialogs.push(dialog.message());
  await dialog.dismiss();
});
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

const cdp = await page.createCDPSession();
await cdp.send("WebAuthn.enable");
const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
  options: {
    protocol: "ctap2",
    transport: "internal",
    hasResidentKey: true,
    hasUserVerification: true,
    isUserVerified: true,
    automaticPresenceSimulation: true,
  },
});

const navigation = (action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
const signOut = () =>
  navigation(page.evaluate(() => document.querySelector('form[action="/auth/logout"]').requestSubmit()));
const inApp = () => page.url().startsWith(`${APP}/`) && !page.url().includes("/signed-out");
const reachesApp = () =>
  page
    .waitForFunction((app) => location.href.startsWith(`${app}/chat`), { timeout: 15_000 }, APP)
    .then(() => true, () => false);

try {
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", EMAIL);
  await page.type("#password", PASSWORD);
  await navigation(page.click("#kc-login"));
  await secondStep(page); // admins need a second step: the seeded admin's code
  if (!check(inApp(), "password sign-in", page.url())) throw new Error("cannot continue");

  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await navigation(page.click('a[href*="webauthn-register-passwordless"]'));
  check((await page.$eval("h1", (h) => h.textContent)) === "Add a passkey", "Gen9's add-a-passkey page");
  const name = await page.$eval("#passkey-name", (input) => input.value);
  await navigation(page.click("#authenticateWebAuthnButton"));
  check(page.url().startsWith(`${APP}/settings`), "passkey created, back in Settings", page.url());
  check(dialogs.length === 0, "no browser dialog while creating it", dialogs.join(" | "));
  const created = (await passkeys(user.id)).filter((c) => !before.has(c.id));
  check(created.length === 1 && created[0].userLabel === name, "Keycloak stored it under the name on the page", name);
  const { credentials } = await cdp.send("WebAuthn.getCredentials", { authenticatorId });
  check(credentials.length === 1 && credentials[0].isResidentCredential, "discoverable credential on the authenticator");

  await signOut();
  await page.goto(`${APP}/auth/login`);
  check(await reachesApp(), "signed in again by passkey autofill, no password", page.url());

  await signOut();
  const noAutofill = await page.evaluateOnNewDocument(() => {
    PublicKeyCredential.isConditionalMediationAvailable = async () => false;
  });
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  check(!!(await page.$("#password")), "without autofill, the sign-in page waits for the user");
  await navigation(page.click("#authenticateWebAuthnButton"));
  check(inApp(), 'signed in with "Sign in with a passkey"', page.url());
  await page.removeScriptToEvaluateOnNewDocument(noAutofill.identifier);

  const logins = (await admin(`/events?user=${user.id}&type=LOGIN&max=20`)).filter(
    (e) =>
      e.time >= startedAt &&
      e.details?.credential_type === "webauthn-passwordless" &&
      e.details?.web_authn_authenticator_user_verification_checked === "true",
  );
  check(logins.length === 2, "Keycloak logged 2 passkey sign-ins with user verification", `${logins.length}`);

  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await navigation(page.click(`a[aria-label="Remove ${name}"]`));
  check(
    (await page.$eval("h1", (h) => h.textContent)) === `Remove “${name}”?`,
    "Keycloak asks to confirm removing it, by name",
  );
  await navigation(page.click("#kc-accept"));
  check(page.url().startsWith(`${APP}/settings`), "back in Settings", page.url());
  check(!(await page.$(`a[aria-label="Remove ${name}"]`)), "Settings no longer lists it");
  check((await passkeys(user.id)).every((c) => before.has(c.id)), "Keycloak removed it");
  const removals = (await admin(`/events?user=${user.id}&type=REMOVE_CREDENTIAL&max=5`)).filter(
    (e) => e.time >= startedAt && e.details?.credential_user_label === name,
  );
  check(removals.length === 1, "Keycloak logged the removal", `${removals.length}`);
  check(pageErrors.length === 0, "no page errors", pageErrors.join(" | "));
} catch (error) {
  check(false, "run", `${error.message} at ${page.url()}`);
  await page.screenshot({ path: `${new URL(".", import.meta.url).pathname}failure.png` });
} finally {
  // Sign out, so runs don't leave sessions under "Where you're signed in"
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" }).catch(() => {});
  await page
    .evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit())
    .then(() => page.waitForNavigation({ waitUntil: "networkidle0" }))
    .catch(() => {});
  await browser.close();
  for (const credential of await passkeys(user.id)) {
    if (!before.has(credential.id)) await admin(`/users/${user.id}/credentials/${credential.id}`, { method: "DELETE" });
  }
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
