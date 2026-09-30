// Batch 4, sessions ending: two browsers; sign out the other one, the other ones, everywhere; an
// admin disables and enables the account; a normal sign-out. Every path ends in back-channel logout.
import { APP, check, kcdb, navigation, nextWindow, otpPolicy, sh, signInWithPassword, signOut, totp, valkey } from "../lib.mjs";

const kcSessions = (sub) => kcdb(`select count(*) from offline_user_session where user_id = '${sub}' and offline_flag = '0'`);
const webSessions = (sub) => valkey(`SMEMBERS gen9:session-by-sub:${sub}`).split("\n").filter((h) => h && valkey(`EXISTS gen9:session:${h}`) === "1").length;
const backchannelLines = (since) => sh(`docker logs --since ${since} gen9-ui-prod-1 2>&1 | grep backchannel-logout`).out.split("\n").filter(Boolean);
const events = (sub, since) =>
  kcdb(`select coalesce(string_agg(type || coalesce(':' || client_id, '') || coalesce(':' || error, ''), ' ' order by event_time), '') from event_entity where user_id = '${sub}' and event_time >= ${since}`);
// A change to someone's access asks first: the item ends in "…", and the dialog's button says it again
const menu = async (page, email, label) => {
  await page.click(`button[aria-label="Actions for ${email}"]`);
  await page.waitForSelector("[role=menuitem]");
  for (const item of await page.$$("[role=menuitem]")) if ((await item.evaluate((el) => el.textContent.trim())) === `${label}…`) await item.click();
  await page.waitForSelector("[role=alertdialog]");
  for (const button of await page.$$("[role=alertdialog] button")) if ((await button.evaluate((el) => el.textContent.trim())) === label) await button.click();
  await page.waitForNetworkIdle({ idleTime: 500 });
};

// What a person sees once a sign-out is done: Settings' toast (sonner). Waiting for the network to go
// quiet instead once timed out after the sign-out had worked (the full run).
const toastText = (page, pattern) =>
  page.waitForFunction((source) => [...document.querySelectorAll("[data-sonner-toast]")].map((t) => t.textContent.trim()).find((t) => new RegExp(source).test(t)) || false,
    { timeout: 30_000 }, pattern.source).then((h) => h.jsonValue());

export default async function sessions(ctx) {
  const { page, rec, user, browser } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  const stamp = () => new Date(Date.now() - 1000).toISOString();
  const other = await (await browser.createBrowserContext()).newPage();
  const signInOther = async () => {
    await signInWithPassword(other, user.email, user.password);
    user.otpCounter = await nextWindow(policy, user.otpCounter);
    await other.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
    await navigation(other, other.click("#kc-login"));
  };
  const otherSignedOut = async () => {
    await other.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    return !other.url().startsWith(APP);
  };

  // 4.1 Two browsers
  rec.mark("4.1 second browser");
  await signInOther();
  obs.twoBrowsers = { keycloak: kcSessions(user.sub), valkey: webSessions(user.sub) };
  check(obs.twoBrowsers.keycloak === "2" && obs.twoBrowsers.valkey === 2, "two browsers: two Keycloak sessions, two web sessions", JSON.stringify(obs.twoBrowsers));
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  obs.sessionList = await page.$$eval("li", (items) => items.map((li) => li.textContent.replace(/\s+/g, " ").trim()).filter((t) => /on macOS|This browser/.test(t)).slice(0, 4));

  // 4.2 Sign out the other browser from Settings (Keycloak Account API, then back-channel logout)
  rec.mark("4.2 sign out other browser");
  let since = Date.now();
  let from = stamp();
  const buttons = await page.$$('button[aria-label^="Sign out "]');
  await buttons[0].click();
  obs.signOutOtherToast = await toastText(page, /^(Signed out|Couldn’t sign out) /);
  await new Promise((resolve) => setTimeout(resolve, 1000));
  obs.signOutOtherHops = rec.hops("4.2 sign out other browser", ["Fetch"]).map(({ id, ...h }) => h);
  obs.signOutOtherEvents = events(user.sub, since);
  obs.signOutOtherBackchannel = backchannelLines(from);
  obs.afterSignOutOther = { keycloak: kcSessions(user.sub), valkey: webSessions(user.sub) };
  check(obs.afterSignOutOther.keycloak === "1" && obs.afterSignOutOther.valkey === 1 && obs.signOutOtherBackchannel.length >= 1 && /^Signed out/.test(obs.signOutOtherToast),
    "one session ended in Keycloak, Keycloak told gen9-ui (back-channel), its web session is gone", `${JSON.stringify(obs.afterSignOutOther)}; ${obs.signOutOtherToast}`);
  check(await otherSignedOut(), "the other browser is asked to sign in on its next request");

  // 4.3 Sign out other sessions
  rec.mark("4.3 sign out other sessions");
  await signInOther();
  since = Date.now();
  from = stamp();
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  for (const b of await page.$$("button")) if ((await b.evaluate((el) => el.textContent.trim())) === "Sign out other sessions") await b.click();
  obs.signOutOthersToast = await toastText(page, /^(Signed out \d|Couldn’t sign out the other)/);
  await new Promise((resolve) => setTimeout(resolve, 1000));
  obs.signOutOthersEvents = events(user.sub, since);
  check((await otherSignedOut()) && kcSessions(user.sub) === "1" && /^Signed out 1 other session\.$/.test(obs.signOutOthersToast), "Sign out other sessions: the other browser out, this one still in", obs.signOutOthersToast);

  // 4.4 Sign out everywhere (gen9-agent -> Keycloak Admin API -> back-channel to every session)
  rec.mark("4.4 sign out everywhere");
  await signInOther();
  since = Date.now();
  from = stamp();
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  for (const b of await page.$$("button")) if ((await b.evaluate((el) => el.textContent.trim())) === "Sign out everywhere") await b.click();
  await page.waitForSelector('[role="alertdialog"]');
  for (const b of await page.$$('[role="alertdialog"] button')) if ((await b.evaluate((el) => el.textContent.trim())) === "Sign out everywhere") await b.click();
  await new Promise((resolve) => setTimeout(resolve, 2500));
  obs.everywhereHops = rec.hops("4.4 sign out everywhere", ["Fetch", "Document"]).map(({ id, ...h }) => h);
  obs.everywhereAgentLog = sh(`docker logs --since ${from} gen9-agent-api-1 2>&1 | grep sign-out-everywhere`).out;
  obs.everywhereBackchannel = backchannelLines(from);
  obs.afterEverywhere = { keycloak: kcSessions(user.sub), valkey: webSessions(user.sub), thisPage: page.url().replace(/\?.*/, "?…") };
  check(obs.afterEverywhere.keycloak === "0" && obs.afterEverywhere.valkey === 0, "Sign out everywhere: no Keycloak session, no web session left", JSON.stringify(obs.afterEverywhere));
  check(/sign-out-everywhere.* 204/.test(obs.everywhereAgentLog), "the agent did it (POST /v1/me/sign-out-everywhere 204)", obs.everywhereAgentLog.split("\n")[0]);

  // 4.5 An admin disables the account, then enables it
  rec.mark("4.5 admin disable");
  const adminPage = await ctx.adminContext.newPage();
  await signInWithPassword(page, user.email, user.password);
  user.otpCounter = await nextWindow(policy, user.otpCounter);
  await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
  await navigation(page, page.click("#kc-login"));
  from = stamp();
  since = Date.now();
  await adminPage.goto(`${APP}/admin/users`, { waitUntil: "networkidle0" });
  await menu(adminPage, user.email, "Disable account");
  await new Promise((resolve) => setTimeout(resolve, 1500));
  obs.disableBackchannel = backchannelLines(from);
  obs.afterDisable = { enabled: kcdb(`select enabled from user_entity where id = '${user.sub}'`), keycloak: kcSessions(user.sub), valkey: webSessions(user.sub) };
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  obs.disabledSignedOut = !page.url().startsWith(APP);
  await page.type("#username", user.email);
  await page.type("#password", user.password);
  await navigation(page, page.click("#kc-login"));
  obs.disabledMessage = await page.$eval("#input-error, [role=alert], .alert-error", (el) => el.textContent.trim()).catch(() => null);
  obs.disableEvents = events(user.sub, since);
  check(obs.afterDisable.enabled === "f" && obs.afterDisable.valkey === 0 && obs.disabledSignedOut && !page.url().startsWith(APP),
    "disabled: signed out at once, and sign-in refused", obs.disabledMessage);
  await menu(adminPage, user.email, "Enable account");
  check(kcdb(`select enabled from user_entity where id = '${user.sub}'`) === "t", "enabled again");

  // 4.6 A normal sign-out: this app, then Keycloak's end-session endpoint
  rec.mark("4.6 sign out");
  await signInWithPassword(page, user.email, user.password);
  user.otpCounter = await nextWindow(policy, user.otpCounter);
  await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
  await navigation(page, page.click("#kc-login"));
  since = Date.now();
  rec.mark("4.6 sign out");
  await signOut(page);
  obs.signOutHops = rec.hops("4.6 sign out", ["Document"]).map(({ id, ...h }) => h);
  obs.signOutEvents = events(user.sub, since);
  obs.afterSignOut = { keycloak: kcSessions(user.sub), valkey: webSessions(user.sub), cookie: !!(await page.cookies(APP)).find((c) => c.name === "gen9_session") };
  check(obs.afterSignOut.keycloak === "0" && obs.afterSignOut.valkey === 0 && !obs.afterSignOut.cookie && page.url().endsWith("/signed-out"),
    "signed out: no sessions anywhere, cookie deleted, on /signed-out", JSON.stringify(obs.afterSignOut));
  obs.adminEvents = (await import("../lib.mjs")).admin(`/admin-events?max=20&dateFrom=${new Date(Date.now() - 600_000).toISOString().slice(0, 10)}`).then((list) =>
    list.filter((e) => e.resourcePath?.includes(user.sub)).map((e) => `${e.operationType} ${e.resourcePath.replace(user.sub, "<sub>")}`));
  obs.adminEvents = await obs.adminEvents;
  obs.console = rec.consoleMessages.filter((m) => m.step.startsWith("4."));
  return obs;
}
