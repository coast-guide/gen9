// Batch 1, birth: sign up, verify the email, choose a password, land in the app.
import { APP, appdb, check, kcdb, MAILPIT, mailTo, settled, sh, valkey } from "../lib.mjs";

export default async function birth(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const navigation = (action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);

  rec.mark("1.1 create account");
  await page.goto(`${APP}/auth/login?intent=signup`, { waitUntil: "networkidle0" });
  obs.signupHops = rec.hops("1.1 create account", ["Document"]);
  obs.txnKeys = valkey(`--scan --pattern "gen9:auth-txn:*"`).split("\n").filter(Boolean).length;
  const txnKey = valkey(`--scan --pattern "gen9:auth-txn:*"`).split("\n").filter(Boolean)[0];
  obs.txnTtl = txnKey ? Number(valkey(`TTL ${txnKey}`)) : null;
  check(obs.txnKeys >= 1 && obs.txnTtl > 0 && obs.txnTtl <= 600, "a sign-in transaction waits in Valkey, 10 minutes at most", `ttl ${obs.txnTtl}`);
  check((await page.$eval("h1", (h) => h.textContent.trim())) === "Create your Gen9 account", "Keycloak's Gen9-styled sign-up page");

  rec.mark("1.2 submit sign-up");
  await page.type("#email", user.email);
  await page.type("#firstName", "Trace");
  await page.type("#lastName", "Learner");
  ctx.startedAt = Date.now();
  await navigation(page.click('form [type="submit"]'));
  obs.afterSignupHops = rec.hops("1.2 submit sign-up", ["Document"]);
  obs.afterSignupH1 = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => null);
  obs.kcUser = kcdb(`select email_verified, enabled from user_entity where email = '${user.email}'`);
  obs.requiredActions = kcdb(
    `select string_agg(required_action, ',' order by required_action) from user_required_action a join user_entity u on u.id = a.user_id where u.email = '${user.email}'`,
  );
  check(obs.kcUser === "f|t", "Keycloak created the user, email not verified yet", obs.kcUser);

  // Mailpit holds every email, password resets included: its API asks for a password
  obs.mailpitWithout = (await fetch(`${MAILPIT}/api/v1/messages?limit=1`)).status;
  check(obs.mailpitWithout === 401, "Mailpit's API refuses a caller without its password", String(obs.mailpitWithout));
  const mail = await mailTo(user.email, ctx.startedAt, /Verify/i);
  obs.verifySubject = mail?.subject;
  const link = mail?.links.find((l) => l.includes("action-token"));
  check(!!link, "the verification email arrived in Mailpit", obs.verifySubject);

  rec.mark("1.3 open verification link");
  await page.goto(link, { waitUntil: "networkidle0" });
  await settled(page);
  obs.verifyHops = rec.hops("1.3 open verification link", ["Document"]);
  obs.afterVerifyH1 = await page.$eval("h1", (h) => h.textContent.trim()).catch(() => null);
  check(!!(await page.$("#password-new")), "after the link, Keycloak asks for a password (required action)", obs.afterVerifyH1);
  // A first password: its rules before the first try, the label "Password", no other devices to sign out of
  obs.passwordPage = await page.evaluate(() => ({
    hint: document.querySelector("#password-hint")?.textContent.trim() ?? null,
    label: document.querySelector('label[for="password-new"]')?.textContent.trim() ?? null,
    signOutOthers: !!document.querySelector("#logout-sessions"),
  }));
  check(
    obs.passwordPage.hint === "At least 15 characters. Not a common password, and not your email." && obs.passwordPage.label === "Password" && !obs.passwordPage.signOutOthers,
    "the page says the rules before the first try, and asks for a first password as such",
    JSON.stringify(obs.passwordPage),
  );

  rec.mark("1.4 choose password");
  await page.type("#password-new", user.password);
  await page.type("#password-confirm", user.password);
  await navigation(page.click('#kc-passwd-update-form [type="submit"]'));
  obs.passwordHops = rec.hops("1.4 choose password", ["Document", "Fetch"]);
  check(page.url().startsWith(`${APP}/chat`), "signed in and in the app", page.url());

  // What the browser holds, and what the server holds
  const cookies = await page.cookies(APP, "http://localhost:15000");
  obs.cookies = cookies.map((c) => ({ name: c.name, domain: c.domain, path: c.path, httpOnly: c.httpOnly, sameSite: c.sameSite, session: c.session }));
  obs.documentCookie = await page.evaluate(() => document.cookie);
  obs.localStorageKeys = await page.evaluate(() => Object.keys(localStorage));
  check(obs.documentCookie === "", "JavaScript sees no cookies (the session cookie is HttpOnly)", JSON.stringify(obs.documentCookie));
  // Signed up without "Remember me": the session cookie ends with the browser, as Keycloak's does
  const session = obs.cookies.find((c) => c.name === "gen9_session");
  check(session?.session === true && session.httpOnly, "the session cookie is HttpOnly and ends with the browser (no Remember me, no Max-Age)", JSON.stringify(session));
  obs.sessionKeys = valkey(`--scan --pattern "gen9:session*"`).split("\n").filter(Boolean).map((k) => k.replace(/:[^:]+$/, ":…"));
  const sub = kcdb(`select id from user_entity where email = '${user.email}'`);
  user.sub = sub;
  const hashes = valkey(`SMEMBERS gen9:session-by-sub:${sub}`).split("\n").filter(Boolean);
  obs.sessionTtl = hashes[0] ? Number(valkey(`TTL gen9:session:${hashes[0]}`)) : null;
  obs.sessionType = hashes[0] ? valkey(`TYPE gen9:session:${hashes[0]}`) : null;
  obs.sessionValueStart = hashes[0] ? valkey(`GET gen9:session:${hashes[0]}`).slice(0, 12) : null;
  check(hashes.length === 1 && obs.sessionTtl > 1500 && obs.sessionTtl <= 1800, "one web session in Valkey, living as long as the refresh token", `ttl ${obs.sessionTtl}`);
  obs.txnLeft = valkey(`--scan --pattern "gen9:auth-txn:*"`).split("\n").filter(Boolean).length;
  obs.kcSession = kcdb(
    `select count(*) from offline_user_session where user_id = '${sub}' and offline_flag = '0'`,
  );
  check(obs.kcSession === "1", "Keycloak holds one login session for the user", obs.kcSession);
  obs.kcEvents = kcdb(
    `select string_agg(type, ' ' order by event_time) from event_entity where user_id = '${sub}'`,
  );
  obs.appUser = appdb(`select email, created_at is not null from users where sub = '${sub}'`);
  check(obs.appUser.startsWith(user.email), "the agent created the user's row on the first API call (just in time)", obs.appUser);
  obs.agentLog = sh("docker logs --since 2m gen9-agent-api-1 2>&1 | grep -E 'GET /v1/(me|threads)' | tail -3").out
    .replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, "…");
  obs.uiLog = sh("docker logs --since 2m gen9-ui-prod-1 2>&1 | grep -i auth | tail -3").out;
  obs.console = rec.consoleMessages.filter((m) => m.step.startsWith("1."));
  return obs;
}
