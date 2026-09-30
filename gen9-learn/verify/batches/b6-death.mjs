// Batch 6, death: delete one chat, then the account, and see what outlives it (the audit record).
// The account deletion asks for a fresh sign-in when the last one is over 5 minutes old (step-up,
// RFC 9470); set QUICK=1 to skip that wait.
import { APP, admin, appdb, ch, check, kcdb, navigation, nextWindow, otpPolicy, settled, sh, signInWithPassword, totp, valkey } from "../lib.mjs";

// index.html's command (part 7, "What outlives the account"), run as it is there
const DISABLE_TRIGGER =
  "import os, psycopg; c = psycopg.connect(host=os.environ['DATABASE_HOST'], dbname=os.environ['DATABASE_NAME'], user=os.environ['DATABASE_USER'], password=os.environ['DATABASE_PASSWORD']); c.execute('alter table audit_events disable trigger audit_events_append_only')";

export default async function death(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  const signIn = async () => {
    await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
    await settled(page);
    if (await page.$("#password")) {
      await page.type("#username", user.email).catch(() => {});
      await page.type("#password", user.password);
      await navigation(page, page.click("#kc-login"));
    }
    if (await page.$("#otp")) {
      user.otpCounter = await nextWindow(policy, user.otpCounter);
      await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
      await navigation(page, page.click("#kc-login"));
    }
  };
  const ask = async (text) => {
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    await page.type("#composer", text);
    await page.click('button[aria-label="Send"]');
    await page.waitForFunction(() => !document.querySelector('button[aria-label="Stop"]') && document.querySelectorAll("ol[aria-live] > li").length >= 2 && !document.querySelector("ol[aria-live] .animate-pulse"), { timeout: 180_000, polling: 500 });
    await page.waitForNetworkIdle({ idleTime: 500 });
    return page.url().split("/").pop();
  };
  const everything = () => ({
    keycloakUser: kcdb(`select count(*) from user_entity where id = '${user.sub}'`),
    appUser: appdb(`select count(*) from users where sub = '${user.sub}'`),
    threads: appdb(`select count(*) from threads t join users u on u.id = t.user_id where u.sub = '${user.sub}'`),
    checkpoints: appdb(`select count(*) from langgraph.checkpoints where thread_id in (select t.id::text from threads t join users u on u.id = t.user_id where u.sub = '${user.sub}')`),
    langfuseRows: ch(`select count() from events_core where user_id = '${user.sub}' format TSV`),
    webSessions: valkey(`SMEMBERS gen9:session-by-sub:${user.sub}`).split("\n").filter((h) => h && valkey(`EXISTS gen9:session:${h}`) === "1").length,
    keycloakSessions: kcdb(`select count(*) from offline_user_session where user_id = '${user.sub}' and offline_flag = '0'`),
    // The model router's records of the user: spend logs, daily totals, end-user row
    modelRows: sh(
      `docker exec gen9-models-postgres-1 psql -U litellm -d litellm -tAc "select (select count(*) from \\"LiteLLM_SpendLogs\\" where end_user = '${user.sub}') + (select count(*) from \\"LiteLLM_DailyEndUserSpend\\" where end_user_id = '${user.sub}') + (select count(*) from \\"LiteLLM_EndUserTable\\" where user_id = '${user.sub}')"`,
    ).out,
  });

  // 6.1 Delete one chat: its trace in Langfuse, its row, its runs and its LangGraph checkpoints go
  rec.mark("6.1 delete chat");
  await signIn();
  const doomed = await ask("Say hi in two words.");
  await ask("Say bye in two words.");
  const traces = () => ch(`select count() from events_core where session_id = '${doomed}' format TSV`);
  // Langfuse ingests asynchronously: wait until the chat is traced, so its erasure is observable
  for (let i = 0; i < 45 && traces() === "0"; i++) await new Promise((resolve) => setTimeout(resolve, 2000));
  const before = { checkpoints: appdb(`select count(*) from langgraph.checkpoints where thread_id = '${doomed}'`), runs: appdb(`select count(*) from runs where thread_id = '${doomed}'`), traces: traces() };
  await page.goto(`${APP}/chat/${doomed}`, { waitUntil: "networkidle0" });
  await page.click('button[aria-label="Chat options"]');
  await page.waitForSelector("[role=menuitem]");
  for (const item of await page.$$("[role=menuitem]")) if (/delete/i.test(await item.evaluate((el) => el.textContent))) await item.click();
  const confirmButton = await page.waitForSelector('[role="alertdialog"] button:last-of-type', { timeout: 5000 }).catch(() => null);
  if (confirmButton) {
    for (const b of await page.$$('[role="alertdialog"] button')) if (/delete/i.test(await b.evaluate((el) => el.textContent))) await b.click();
  }
  await page.waitForNetworkIdle({ idleTime: 800 });
  // Langfuse deletes asynchronously too: allow 3 minutes
  for (let i = 0; i < 90 && traces() !== "0"; i++) await new Promise((resolve) => setTimeout(resolve, 2000));
  obs.deleteChat = {
    before,
    after: { thread: appdb(`select count(*) from threads where id = '${doomed}'`), runs: appdb(`select count(*) from runs where thread_id = '${doomed}'`), checkpoints: appdb(`select count(*) from langgraph.checkpoints where thread_id = '${doomed}'`), traces: traces() },
    agentLog: sh(`docker logs --since 1m gen9-agent-api-1 2>&1 | grep -E '"DELETE /v1/threads' | tail -1`).out.replace(doomed, "<thread id>"),
  };
  check(
    Number(before.traces) > 0 && Object.values(obs.deleteChat.after).every((v) => v === "0"),
    "deleting a chat removed its trace, row, runs and checkpoints",
    `${JSON.stringify(before)} -> ${JSON.stringify(obs.deleteChat.after)}`,
  );

  // 6.2 Delete the account (step-up first when the last sign-in is over 5 minutes old)
  obs.beforeDelete = everything();
  check(obs.beforeDelete.modelRows !== "0", "before: the model router holds the user's usage", `${obs.beforeDelete.modelRows} rows`);
  if (!process.env.QUICK) {
    const authTime = Number(kcdb(`select max(created_on) from offline_user_session where user_id = '${user.sub}' and offline_flag = '0'`));
    const wait = (authTime + 305) * 1000 - Date.now();
    if (wait > 0) console.log(`      (waiting ${Math.round(wait / 1000)} s so the last sign-in is over 5 minutes old)`);
    await new Promise((resolve) => setTimeout(resolve, Math.max(0, wait)));
  }
  rec.mark("6.2 delete account");
  const since = Date.now();
  const from = new Date(since - 1000).toISOString();
  const agentDeletes = () => sh(`docker logs --since ${from} gen9-agent-api-1 2>&1 | grep -E '"DELETE /v1/me'`).out;
  const clickButton = async (selector, label) => {
    for (const b of await page.$$(selector)) if ((await b.evaluate((el) => el.textContent.trim())) === label) return b.click();
    throw new Error(`no "${label}" button`);
  };
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  await clickButton("button", "Delete account");
  await page.waitForSelector('[role="alertdialog"]');
  // gen9-ui knows the session's auth_time: over 5 minutes old, the dialog offers Sign in again
  // instead of the email field, and nothing reaches the agent
  const signInAgain = await page.$('[role="alertdialog"] a[href*="reauth=1"]');
  obs.stepUp = !!signInAgain;
  if (signInAgain) {
    obs.stepUpDialog = await page.$eval('[role="alertdialog"]', (el) => el.innerText.split("\n").filter(Boolean));
    obs.stepUpAgentLog = agentDeletes();
    check(obs.stepUpDialog.some((line) => line.includes("sign in again first")) && obs.stepUpAgentLog === "",
      "last sign-in over 5 minutes old: the dialog asks to sign in again, no request reached the agent", obs.stepUpDialog.find((line) => line.includes("sign in again")));
    rec.mark("6.3 sign in again");
    await navigation(page, signInAgain.click());
    obs.reauthFirstHop = rec.hops("6.3 sign in again", ["Document"]).find((h) => h.url.includes("openid-connect/auth"))?.url.match(/max_age=\d+/)?.[0];
    obs.reauthAsked = { password: !!(await page.$("#password")) };
    if (await page.$("#password")) {
      await page.type("#password", user.password);
      await navigation(page, page.click("#kc-login"));
    }
    obs.reauthAsked.otp = !!(await page.$("#otp"));
    if (await page.$("#otp")) {
      user.otpCounter = await nextWindow(policy, user.otpCounter);
      await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
      await navigation(page, page.click("#kc-login"));
    }
    check(obs.reauthFirstHop === "max_age=0" && obs.reauthAsked.password && /\/settings\?delete=1/.test(page.url()),
      "Sign in again = OIDC max_age=0: Keycloak asked for the password though its session was alive, then back to Settings with the dialog open",
      `${obs.reauthFirstHop}, ${JSON.stringify(obs.reauthAsked)}, ${page.url().replace(APP, "")}`);
  }
  await page.waitForSelector("#delete-confirmation");
  await page.type("#delete-confirmation", user.email);
  await clickButton('[role="alertdialog"] button', "Delete account");
  await page.waitForNavigation({ waitUntil: "networkidle0", timeout: 30_000 }).catch(() => {});
  obs.landedOn = page.url();
  obs.deleteAgentLog = agentDeletes().split("\n").filter(Boolean);
  check(obs.deleteAgentLog.length === 1 && / 204 /.test(obs.deleteAgentLog[0]), "the agent got one DELETE /v1/me, with a fresh auth_time: 204", obs.deleteAgentLog.map((l) => l.replace(/.*"DELETE/, '"DELETE')).join(" | "));
  obs.adminEvents = (await admin(`/admin-events?max=50`)).filter((e) => e.resourcePath?.includes(user.sub) && e.time >= since).map((e) => `${e.operationType} ${e.resourcePath.replace(user.sub, "<sub>")}`).reverse();
  obs.backchannel = sh(`docker logs --since ${from} gen9-ui-prod-1 2>&1 | grep backchannel-logout`).out.split("\n").filter(Boolean);
  // Langfuse erases asynchronously, behind whatever else its worker has queued: allow 3 minutes
  let after = everything();
  for (let i = 0; i < 90 && after.langfuseRows !== "0"; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    after = everything();
  }
  obs.afterDelete = after;
  check(Object.values(after).every((v) => String(v) === "0"), "deleted everywhere: Keycloak, app rows, chats, memory, traces, sessions, the model router's records", JSON.stringify(after));
  user.deleted = true;

  // 6.4 What outlives the account: the audit record, by sub alone, and it refuses to change
  rec.mark("6.4 audit record");
  const theirs = `from audit_events where actor = '${user.sub}'`;
  obs.auditAfter = appdb(`select string_agg(action || ' ' || outcome, ', ' order by id) ${theirs}`);
  obs.auditLatest = appdb(`select actor = '${user.sub}' from audit_events where action = 'account.delete' order by id desc limit 1`);
  obs.auditEmail = appdb(`select count(*) from audit_events a where actor = '${user.sub}' and a::text ilike '%${user.email}%'`);
  obs.auditRefuses = sh(`docker exec gen9-postgres-postgres-1 psql -U postgres -d gen9_agent -c "delete ${theirs}"`).out;
  obs.auditRows = appdb(`select count(*) ${theirs}`);
  check(
    /task\.trigger\.make success.*account\.delete success/.test(obs.auditAfter) && obs.auditLatest === "t" && obs.auditEmail === "0" && /append-only/.test(obs.auditRefuses) && obs.auditRows !== "0",
    "the audit record outlives the account: its actions by sub, no email, and a DELETE (even the superuser's) is refused",
    `${obs.auditAfter}; email in rows: ${obs.auditEmail}; ${obs.auditRefuses.split("\n")[0]}; rows: ${obs.auditRows}`,
  );
  // Only the table's owner could switch the trigger off, and the services aren't it: they connect
  // as gen9_agent_app, which owns nothing. The page's command, from the API's container
  obs.auditOwner = appdb("select tableowner from pg_tables where tablename = 'audit_events'");
  obs.servicesDbUser = ["api", "worker"].map((c) => sh(`docker exec gen9-agent-${c}-1 printenv DATABASE_USER`).out).join(" ");
  obs.apiDisables = sh(`docker exec gen9-agent-api-1 python -c "${DISABLE_TRIGGER}"`).out.split("\n").at(-1);
  check(
    obs.auditOwner === "gen9_agent" && obs.servicesDbUser === "gen9_agent_app gen9_agent_app" && /must be owner of table audit_events/.test(obs.apiDisables),
    "the owner is gen9_agent, the API and worker connect as gen9_agent_app, and the API's role can't switch the trigger off",
    `${obs.auditOwner}; ${obs.servicesDbUser}; ${obs.apiDisables}`,
  );
  obs.console = rec.consoleMessages.filter((m) => m.step.startsWith("6."));
  return obs;
}
