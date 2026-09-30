// Batch 2w, handing it work: a scheduled task (its Temporal Schedule, Run now and its chat), its API
// trigger (a token shown once, fired over HTTP with a payload), and the audit record of the token.
// The token is used and dropped, never printed or recorded.
import { API, APP, appdb, check, sh } from "../lib.mjs";

const until = async (test, seconds) => {
  for (let i = 0; i < seconds; i++) {
    if (await test()) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
};

export default async function work(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const name = "Morning brief";
  const row = async () =>
    (await page.$$eval('ul[aria-label="Scheduled tasks"] > li', (lis, n) => lis.map((li) => li.innerText.replace(/\s+/g, " ")).find((t) => t.includes(n)) ?? "", name)).slice(0, 200);
  const menu = async (item) => {
    await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
    await page.click(`button[aria-label="Actions for ${name}"]`);
    await page.waitForSelector("[role=menuitem]");
    for (const el of await page.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === item) return el.click();
    throw new Error(`no ${item} in the task's menu`);
  };

  // 2w.1 A task: every day at 09:00
  rec.mark("2w.1 new task");
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  for (const b of await page.$$("main button")) if ((await b.evaluate((e) => e.textContent.trim())) === "New task") await b.click();
  await page.waitForSelector("#new-name");
  await page.type("#new-name", name);
  await page.type("#new-prompt", "In one sentence, tell me one fact about lighthouses.");
  await page.select("#new-kind", "daily");
  await page.$eval("#new-time", (el) => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set.call(el, "09:00");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.click('form[aria-label="New task"] button[type="submit"]');
  await page.waitForFunction((n) => [...document.querySelectorAll('ul[aria-label="Scheduled tasks"] li')].some((li) => li.textContent.includes(n)), { timeout: 20_000 }, name);
  const taskId = appdb(`select t.id from tasks t join users u on u.id = t.user_id where u.email = '${user.email}' and t.name = '${name}'`);
  ctx.taskId = taskId;
  obs.taskRow = await row();
  obs.taskRowSql = appdb(`select name || ' | ' || (schedule->>'kind') || ' ' || (schedule->>'time') || ' | ' || time_zone || ' | ' || status from tasks where id = '${taskId}'`);
  obs.schedule = sh(`docker compose -f gen9-temporal/compose.yaml run --rm cli temporal schedule list 2>/dev/null | grep task-${taskId}`).out.replace(taskId, "<task id>").replace(/\s+/g, " ").trim();
  check(!!taskId && obs.schedule.includes("task-<task id>") && obs.schedule.includes("TaskFiringWorkflow"), "the task is a Temporal Schedule, task-<task id>, firing TaskFiringWorkflow", obs.schedule);

  // 2w.2 Run now: a chat of its own, named after the task
  rec.mark("2w.2 run now");
  await menu("Run now");
  const answered = () => appdb(`select count(*) from runs r join threads t on t.id = r.thread_id where t.task_id = '${taskId}' and r.status = 'success'`);
  const ran = await until(() => answered() === "1", 240);
  obs.firstChat = appdb(`select title from threads where task_id = '${taskId}' order by created_at limit 1`);
  obs.firings = sh(`docker compose -f gen9-temporal/compose.yaml run --rm cli temporal workflow list --query "Gen9User='${user.sub}' AND (WorkflowType='TaskFiringWorkflow' OR WorkflowType='RunWorkflow')" --limit 4 2>/dev/null`)
    .out.replace(/[0-9a-f]{8}-[0-9a-f-]{27}(-now-[0-9a-f]+)?/g, "<id>").replace(/\d+ (seconds?|minutes?) ago/g, "<when>");
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  obs.taskRowAfter = await row();
  check(ran && obs.firstChat === name && /TaskFiringWorkflow/.test(obs.firings), "Run now makes a chat named after the task, through TaskFiringWorkflow and its run", `${obs.firstChat}; ${obs.firings.split("\n").slice(1, 3).join(" | ")}`);

  // 2w.3 The API trigger: a token shown once, fired from outside with a payload
  rec.mark("2w.3 trigger");
  await menu("API trigger…");
  const dialog = await page.waitForSelector('[role="alertdialog"]');
  for (const b of await page.$$('[role="alertdialog"] button')) if (/Make (a|a new) token/.test(await b.evaluate((e) => e.textContent.trim()))) await b.click();
  const field = await page.waitForSelector(`[id="${taskId}-token"]`);
  const token = await field.evaluate((e) => e.value);
  obs.triggerDialog = (await dialog.evaluate((d) => d.innerText.replace(/\s+/g, " "))).replaceAll(token, "…").replaceAll(taskId, "<task id>").slice(0, 400);
  for (const b of await page.$$('[role="alertdialog"] button')) if ((await b.evaluate((e) => e.textContent.trim())) === "Done") await b.click();
  obs.hashStored = appdb(`select trigger_hash is not null and length(trigger_hash) = 64 from tasks where id = '${taskId}'`);
  const fired = await fetch(`${API}/v1/tasks/${taskId}/fire`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ text: "The keeper's log says: fog at dawn." }),
  });
  obs.fired = `${fired.status} ${(await fired.text()).replace(taskId, "<task id>")}`;
  const twice = await until(() => answered() === "2", 240);
  obs.payloadInMessage = appdb(`select position('fog at dawn' in r.input->>'message') > 0 from runs r join threads t on t.id = r.thread_id where t.task_id = '${taskId}' order by r.created_at desc limit 1`);
  const wrong = await fetch(`${API}/v1/tasks/${taskId}/fire`, { method: "POST", headers: { Authorization: "Bearer not-the-token" } });
  obs.wrongToken = wrong.status;
  check(
    /^202 /.test(obs.fired) && twice && obs.payloadInMessage === "t" && obs.hashStored === "t" && obs.wrongToken === 401,
    "the trigger fires with its token (202, another chat, the payload in its message), refuses another token (401), and only its hash is kept",
    `${obs.fired}; payload in message: ${obs.payloadInMessage}; wrong token: ${obs.wrongToken}`,
  );

  // 2w.4 Who did what: the audit record has the token's making, by the person
  rec.mark("2w.4 audit");
  obs.audit = appdb(`select string_agg(action || ' ' || outcome, ', ' order by id) from audit_events where actor = '${user.sub}'`);
  check(/task\.trigger\.make success/.test(obs.audit), "the audit record has the token's making, with the person as actor", obs.audit);
  return obs;
}
