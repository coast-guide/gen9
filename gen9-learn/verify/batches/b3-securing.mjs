// Batch 3, securing it: authenticator app and recovery codes (Keycloak account actions started from
// Settings), signing in with them, a lockout that an admin clears, forgot password with an
// authenticator app, a passkey, and a password change.
import { APP, admin, check, kcdb, kcEnv, mailTo, navigation, nextWindow, otpPolicy, settled, signInWithPassword, signOut, totp, valkey } from "../lib.mjs";
import { secondStep } from "../../../e2e/second-step.mjs";

const clickText = async (page, selector, pattern) => {
  const handles = await page.$$(selector);
  for (const handle of handles) {
    const text = await handle.evaluate((el) => el.textContent.trim());
    if (pattern.test(text)) return navigation(page, handle.click());
  }
  throw new Error(`no ${selector} matching ${pattern}`);
};
// The toast Settings shows on return from an account action (sonner)
const toastText = (page) =>
  page.waitForFunction(() => document.querySelector("[data-sonner-toast]")?.textContent.trim() || false, { timeout: 10_000 })
    .then((h) => h.jsonValue()).catch(() => null);
const credentials = (sub) =>
  kcdb(`select coalesce(string_agg(type, ', ' order by type), '(none)') from credential where user_id = '${sub}'`);
const events = (sub, since) =>
  kcdb(`select coalesce(string_agg(type || coalesce(':' || error, ''), ' ' order by event_time), '') from event_entity where user_id = '${sub}' and event_time >= ${since}`);

export default async function securing(ctx) {
  const { page, rec, user, browser } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  obs.otpPolicy = policy;

  // 3.1 Authenticator app: a Keycloak account action (kc_action) started from Settings
  rec.mark("3.1 add authenticator app");
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  const cookieBefore = (await page.cookies(APP)).find((c) => c.name === "gen9_session")?.value;
  const sessionsBefore = valkey(`SMEMBERS gen9:session-by-sub:${user.sub}`);
  let since = Date.now();
  // Keycloak asks for the password again when the last sign-in is over its max auth age (300 s by
  // default for an action an app starts). A reader always is by now; the verifier too, without QUICK
  const lastSignIn = Math.max(...(await admin(`/users/${user.sub}/sessions`)).map((s) => s.start));
  obs.lastSignInAge = Math.round((Date.now() - lastSignIn) / 1000);
  await navigation(page, page.click('a[href*="action=CONFIGURE_TOTP"]'));
  // The click goes through gen9-ui's redirect to Keycloak's page: wait for the page, not the first hop
  await page.waitForSelector("#totpSecret, #password", { timeout: 30_000 });
  obs.totpAsksPassword = !!(await page.$("#password"));
  if (obs.totpAsksPassword) {
    obs.totpReauthHeading = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => null);
    obs.totpReauthUsername = await page.$eval("#username", (i) => i.type).catch(() => "none");
    await page.type("#password", user.password);
    await navigation(page, page.click("#kc-login"));
    await page.waitForSelector("#totpSecret", { timeout: 30_000 });
  }
  check(obs.totpAsksPassword === obs.lastSignInAge > 300, "Keycloak asks for the password first only when the last sign-in is over 300 s old",
    `last sign-in ${obs.lastSignInAge} s ago; asked: ${obs.totpAsksPassword}${obs.totpReauthHeading ? ` (${obs.totpReauthHeading})` : ""}`);
  user.totpSecret = await page.$eval("#totpSecret", (input) => input.value);
  user.otpCounter = Math.floor(Date.now() / 1000 / policy.period);
  await page.type("#totp", totp(user.totpSecret, policy, user.otpCounter));
  await navigation(page, page.click("#saveTOTPBtn"));
  // A first authenticator app goes straight on to recovery codes, so a lost phone doesn't lock the
  // person out: the callback returns to /settings?next=recovery-codes, which starts that action
  await page.waitForSelector('input[name="generatedRecoveryAuthnCodes"]', { timeout: 30_000 });
  obs.totpHops = rec.hops("3.1 add authenticator app", ["Document"]);
  check(obs.totpHops.some((h) => h.url.includes("kc_action=CONFIGURE_TOTP")) && obs.totpHops.some((h) => h.url.includes("kc_action_status=success")),
    "the round trip: kc_action=CONFIGURE_TOTP out, kc_action_status=success back");
  check(obs.totpHops.some((h) => h.url.includes("next=recovery-codes")) && obs.totpHops.some((h) => h.url.includes("kc_action=CONFIGURE_RECOVERY_AUTHN_CODES")),
    "a first authenticator app goes straight on to recovery codes (/settings?next=recovery-codes, then kc_action=CONFIGURE_RECOVERY_AUTHN_CODES)");
  obs.credentialsAfterTotp = credentials(user.sub);
  obs.totpEvents = events(user.sub, since);
  check(/otp/.test(obs.credentialsAfterTotp), "Keycloak stores an otp credential", obs.credentialsAfterTotp);

  // 3.2 Recovery codes, the page the first app led to
  rec.mark("3.2 recovery codes");
  since = Date.now();
  // A comma-separated list in a hidden field; never printed
  user.recoveryCodes = (await page.$eval('input[name="generatedRecoveryAuthnCodes"]', (i) => i.value)).split(",");
  obs.recoveryCodeCount = user.recoveryCodes.length;
  obs.recoveryHeading = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => null);
  await page.click("#kcRecoveryCodesConfirmationCheck");
  await navigation(page, page.click("#saveRecoveryAuthnCodesBtn"));
  obs.recoveryHops = rec.hops("3.2 recovery codes", ["Document"]);
  check(page.url().startsWith(`${APP}/settings`), "back in Settings after both account actions", page.url());
  obs.codesToast = await toastText(page);
  check(obs.codesToast === "Authenticator app set up. Recovery codes saved.", "Settings names both actions", obs.codesToast);
  // Keycloak's default count, and Settings' count of those left
  obs.codesLeft = await page.evaluate(() => document.body.innerText.match(/\d+ of \d+ left/)?.[0] ?? null);
  check(obs.recoveryCodeCount === 12 && obs.codesLeft === "12 of 12 left", "12 numbered codes, and Settings says 12 of 12 left", `${obs.recoveryCodeCount} codes; ${obs.codesLeft}`);
  const cookieAfter = (await page.cookies(APP)).find((c) => c.name === "gen9_session")?.value;
  obs.newWebSession = cookieAfter !== cookieBefore;
  obs.oldSessionGone = !valkey(`SMEMBERS gen9:session-by-sub:${user.sub}`).includes(sessionsBefore.split("\n")[0]);
  check(obs.newWebSession && obs.oldSessionGone, "the callback minted a new web session id and dropped the old one (no session fixation)");
  obs.credentialsAfterCodes = credentials(user.sub);
  obs.codesEvents = events(user.sub, since);
  check(/recovery-authn-codes/.test(obs.credentialsAfterCodes), `${obs.recoveryCodeCount} recovery codes stored`, `${obs.credentialsAfterCodes} (${obs.recoveryHeading})`);

  // 3.3 Sign in with the second step
  rec.mark("3.3 sign in with code");
  await signOut(page);
  since = Date.now();
  await signInWithPassword(page, user.email, user.password);
  check(!!(await page.$("#otp")), "after the password, Keycloak asks for the authenticator code");
  user.otpCounter = await nextWindow(policy, user.otpCounter);
  await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
  await navigation(page, page.click("#kc-login"));
  check(page.url().startsWith(APP), "signed in with password + code", page.url());
  obs.codeSignInHops = rec.hops("3.3 sign in with code", ["Document"]);

  // 3.4 Try another way: a recovery code
  rec.mark("3.4 sign in with recovery code");
  await signOut(page);
  await signInWithPassword(page, user.email, user.password);
  // Keycloak's pattern: a small form posting tryAnotherWay=on (gen9-keycloak/theme/src/login/Template.tsx)
  await navigation(page, page.click("#kc-select-try-another-way-form button"));
  obs.anotherWayOptions = await page.$$eval("form button, form a, .select-auth-box-parent", (els) => els.map((e) => e.textContent.trim().replace(/\s+/g, " ")).filter(Boolean));
  await clickText(page, "form button, .select-auth-box-parent, form a", /recovery/i);
  await page.type("#recoveryCodeInput", user.recoveryCodes[0]);
  await navigation(page, page.click("#kc-login"));
  check(page.url().startsWith(APP), "signed in with password + recovery code #1", page.url());
  obs.recoverySignInEvents = events(user.sub, since);

  // 3.5 Lockout, cleared by an admin (Ada, from the Users page)
  rec.mark("3.5 lockout");
  await signOut(page);
  since = Date.now();
  for (let i = 0; i < 5; i++) await signInWithPassword(page, user.email, `wrong-${i}-password-xx`);
  await signInWithPassword(page, user.email, user.password);
  obs.lockedMessage = await page.$eval("#input-error, .alert-error, [role=alert]", (el) => el.textContent.trim()).catch(() => null);
  check(!!(await page.$("#password")) && !page.url().startsWith(APP), "after 5 wrong passwords the right one is refused too", obs.lockedMessage);
  // Keycloak 26 keeps brute-force state in its cache, not in the login_failure table
  obs.loginFailureTable = kcdb(`select count(*) from login_failure where user_id = '${user.sub}'`);
  obs.bruteForce = await admin(`/attack-detection/brute-force/users/${user.sub}`);
  obs.lockoutEvents = events(user.sub, since);
  const adminContext = await browser.createBrowserContext();
  const adminPage = await adminContext.newPage();
  await signInWithPassword(adminPage, kcEnv.GEN9_SEED_ADMIN_EMAIL, kcEnv.GEN9_SEED_ADMIN_PASSWORD);
  await secondStep(adminPage); // admins need a second step: the seeded admin's code
  await adminPage.goto(`${APP}/admin/users`, { waitUntil: "networkidle0" });
  obs.lockedBadge = await adminPage.evaluate((email) => [...document.querySelectorAll("li, tr")].find((row) => row.textContent.includes(email))?.textContent.includes("Locked"), user.email);
  await adminPage.click(`button[aria-label="Actions for ${user.email}"]`);
  await adminPage.waitForSelector("[role=menuitem]");
  const items = await adminPage.$$("[role=menuitem]");
  for (const item of items) if ((await item.evaluate((el) => el.textContent.trim())) === "Unlock sign-in") await item.click();
  await adminPage.waitForNetworkIdle({ idleTime: 500 });
  ctx.adminContext = adminContext;
  obs.bruteForceAfterUnlock = await admin(`/attack-detection/brute-force/users/${user.sub}`);
  check(obs.bruteForce.disabled && obs.lockedBadge && !obs.bruteForceAfterUnlock.disabled && obs.bruteForceAfterUnlock.numFailures === 0,
    "Keycloak's lockout state (Admin API) was set, Ada saw the Locked badge, and Unlock sign-in cleared it", `failures ${obs.bruteForce.numFailures}`);

  // 3.6 Forgot password: the link asks for the authenticator code first (gen9-reset-credentials)
  rec.mark("3.6 forgot password");
  since = Date.now();
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await navigation(page, page.click('a[href*="reset-credentials"]'));
  await page.type("#username", user.email);
  await navigation(page, page.click('#kc-reset-password-form [type="submit"]'));
  const mail = await mailTo(user.email, since, /password/i);
  await page.goto(mail.links.find((l) => l.includes("action-token")), { waitUntil: "networkidle0" });
  await settled(page);
  check(!!(await page.$("#otp")), "the reset link asks for the authenticator code first");
  user.otpCounter = await nextWindow(policy, user.otpCounter);
  await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
  await navigation(page, page.click("#kc-login"));
  user.password = `${user.password}-2`;
  await page.type("#password-new", user.password);
  await page.type("#password-confirm", user.password);
  await navigation(page, page.click('#kc-passwd-update-form [type="submit"]'));
  check(page.url().startsWith(APP), "new password set after the code, signed in", page.url());
  obs.resetEvents = events(user.sub, since);

  // 3.7 A passkey (Chrome's virtual authenticator stands in for Touch ID)
  rec.mark("3.7 passkey");
  const cdp = await page.createCDPSession();
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await navigation(page, page.click('a[href*="webauthn-register-passwordless"]'));
  await navigation(page, page.click("#authenticateWebAuthnButton"));
  obs.passkeyToast = await toastText(page);
  check(obs.passkeyToast === "Passkey added.", "Settings says the passkey was added", obs.passkeyToast);
  obs.credentialsAfterPasskey = credentials(user.sub);
  await signOut(page);
  since = Date.now();
  const noAutofill = await page.evaluateOnNewDocument(() => {
    PublicKeyCredential.isConditionalMediationAvailable = async () => false;
  });
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await navigation(page, page.click("#authenticateWebAuthnButton"));
  await page.removeScriptToEvaluateOnNewDocument(noAutofill.identifier);
  check(page.url().startsWith(APP), "signed in with the passkey alone: no password, no code", page.url());
  // Detached, or passkey autofill would sign this browser in by itself on every later sign-in page
  await cdp.send("WebAuthn.removeVirtualAuthenticator", { authenticatorId });
  obs.passkeyEvents = kcdb(
    `select string_agg(type || ' ' || coalesce(details_json_long_value, details_json, ''), ' | ') from event_entity where user_id = '${user.sub}' and event_time >= ${since} and type = 'LOGIN'`,
  ).replace(/"(code_id|redirect_uri|auth_method|username|consent)":"[^"]*",?/g, "");

  // 3.8 Change password from Settings
  rec.mark("3.8 change password");
  since = Date.now();
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await navigation(page, page.click('a[href*="action=UPDATE_PASSWORD"]'));
  obs.changePasswordFirstPage = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => null);
  if (await page.$("#password") && !(await page.$("#password-new"))) {
    await page.type("#password", user.password);
    await navigation(page, page.click("#kc-login"));
  }
  user.password = `${user.password}-3`;
  await page.type("#password-new", user.password);
  await page.type("#password-confirm", user.password);
  await navigation(page, page.click('#kc-passwd-update-form [type="submit"]'));
  check(page.url().startsWith(`${APP}/settings`), "password changed, back in Settings", page.url());
  obs.passwordToast = await toastText(page);
  check(obs.passwordToast === "Password changed.", "Settings says the password was changed", obs.passwordToast);
  obs.changePasswordEvents = events(user.sub, since);
  obs.finalCredentials = credentials(user.sub);
  obs.adminEvents = (await admin(`/admin-events?resourcePath=${encodeURIComponent(`attack-detection/brute-force/users/${user.sub}`)}&max=5`)).map((e) => `${e.operationType} ${e.resourcePath.replace(user.sub, "<sub>")}`);
  obs.console = rec.consoleMessages.filter((m) => m.step.startsWith("3."));
  return obs;
}
