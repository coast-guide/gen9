// Batch 3, deeper: the rest of handing Gen9 work, as the same person, after b2w.
//   outcomes    a task with "Done when" (a rubric): each run graded, a short one tried again in the
//               same chat with the grader's findings, until the rubric is met
//   notices     the one email the firing sends when it's done, and what it leaves out; Settings'
//               choices for these emails
//   background  a task the agent starts in the background: a chat of its own, which tells the chat
//               that started it when it's done (TellChatWorkflow)
// What ran is read from Postgres and Temporal; emails from Mailpit.
import { randomBytes } from "node:crypto";
import { APP, MAILPIT, appdb, check, kcEnv, mailTo, sh } from "../lib.mjs";
import { chatHelpers, runsOf, until } from "../chat.mjs";

const CLOSING = "Checked by Gen9";

export default async function workDeeper(ctx) {
  const { page, user } = ctx;
  const obs = {};
  const { newChat, send } = chatHelpers(page);
  const temporal = (args) => sh(`docker compose -f gen9-temporal/compose.yaml run --rm cli temporal ${args} 2>/dev/null`, { timeout: 90_000 }).out;
  // The Activities a workflow ran, in order, from its history
  const activitiesOf = (id) => {
    try {
      const history = JSON.parse(temporal(`workflow show -w ${id} -o json`));
      return (history.events ?? [])
        .filter((e) => e.eventType === "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED")
        .map((e) => e.activityTaskScheduledEventAttributes.activityType.name);
    } catch {
      return [];
    }
  };

  // ---- outcomes: a task with Done when, run now
  const name = "Primary colours";
  const row = async () =>
    (await page.$$eval('ul[aria-label="Scheduled tasks"] > li', (lis, n) => lis.map((li) => li.innerText.replace(/\s+/g, " ")).find((t) => t.includes(n)) ?? "", name)).slice(0, 240);
  const since = Date.now();
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  for (const b of await page.$$("main button")) if ((await b.evaluate((e) => e.textContent.trim())) === "New task") await b.click();
  await page.waitForSelector("#new-name");
  await page.type("#new-name", name);
  await page.type("#new-prompt", "Name three primary colours, one per line.");
  await page.select("#new-kind", "daily");
  obs.triesBeforeRubric = !!(await page.$("#new-tries"));
  obs.rubricLabel = await page.$eval("#new-rubric", (t) => t.closest("label")?.querySelector("span")?.textContent.trim()).catch(() => null);
  await page.type("#new-rubric", `- Names three colours, one per line\n- Its last line is exactly: ${CLOSING}`);
  await page.waitForSelector("#new-tries");
  obs.triesLabel = await page.$eval("#new-tries", (t) => t.closest("label")?.querySelector("span")?.textContent.trim()).catch(() => null);
  await page.click('form[aria-label="New task"] button[type="submit"]');
  await page.waitForFunction((n) => [...document.querySelectorAll('ul[aria-label="Scheduled tasks"] li')].some((li) => li.textContent.includes(n)), { timeout: 20_000 }, name);
  const taskId = appdb(`select t.id from tasks t join users u on u.id = t.user_id where u.email = '${user.email}' and t.name = '${name}'`);
  obs.taskRow = await row();
  obs.taskSql = appdb(`select max_iterations || '|' || length(rubric) from tasks where id = '${taskId}'`);
  check(!!taskId && obs.taskRow.includes("Checked against a rubric, 3 tries at most"), "the task keeps its rubric, and its row says so", `${obs.rubricLabel}; ${obs.triesLabel}; ${obs.taskRow}`);

  await page.click(`button[aria-label="Actions for ${name}"]`);
  await page.waitForSelector("[role=menuitem]");
  for (const el of await page.$$("[role=menuitem]")) if ((await el.evaluate((e) => e.textContent.trim())) === "Run now") await el.click();
  const chatOf = () => appdb(`select id from threads where task_id = '${taskId}' order by created_at limit 1`);
  const verdicts = (chat) => appdb(`select coalesce(string_agg(e.result, ',' order by e.iteration), '') from outcome_evaluations e join runs r on r.id = e.run_id where r.thread_id = '${chat}'`);
  const over = () => {
    const chat = chatOf();
    const v = chat ? verdicts(chat) : "";
    const idle = chat && !/queued|running|waiting/.test(runsOf(chat));
    return idle && (v.endsWith("satisfied") || v.split(",").length >= 3) && v;
  };
  obs.verdicts = await until(over, 360);
  const chat = chatOf();
  obs.runs = runsOf(chat);
  obs.secondMessage = appdb(`select left(input->>'message', 220) from runs where thread_id = '${chat}' order by created_at offset 1 limit 1`);
  obs.criteria = appdb(`select e.iteration || ' ' || e.result || ' ' || e.criteria::text from outcome_evaluations e join runs r on r.id = e.run_id where r.thread_id = '${chat}' order by e.iteration`)
    .slice(0, 600);
  await page.goto(`${APP}/scheduled`, { waitUntil: "networkidle0" });
  obs.taskRowAfter = await row();
  const firing = temporal(`workflow list --query "Gen9User='${user.sub}' AND WorkflowType='TaskFiringWorkflow'" --limit 1 -o json`);
  let firingId = null;
  try {
    firingId = JSON.parse(firing)[0]?.execution?.workflowId ?? null;
  } catch {}
  obs.firingActivities = firingId ? activitiesOf(firingId).join(", ") : "";
  const tries = obs.runs.split(",").length;
  check(
    /satisfied$/.test(obs.verdicts ?? "") && tries >= 2 && /try 1 of 3/.test(obs.secondMessage ?? ""),
    "the first answer is graded short of the rubric, and a later run in the same chat, given the grader's findings, meets it",
    `${obs.verdicts}; runs ${obs.runs}; ${obs.taskRowAfter}`,
  );
  check(/grade_run/.test(obs.firingActivities) && /continue_task/.test(obs.firingActivities) && /notify_outcome/.test(obs.firingActivities), "the firing's workflow grades, continues and notifies, each an Activity", obs.firingActivities);
  await page.goto(`${APP}/chat/${chat}`, { waitUntil: "networkidle0" });
  // The grader's findings, shown as its, without the block marks that tell the agent they're data
  obs.rubricCheck = await page.$$eval("ol[aria-live] > li", (lis) => lis.map((li) => li.innerText).find((t) => t.startsWith("From the rubric check")) ?? "");
  check(
    /try 1 of 3/.test(obs.rubricCheck) && !obs.rubricCheck.includes("<grader-findings>") && /<grader-findings>/.test(appdb(`select input->>'message' from runs where thread_id = '${chat}' order by created_at offset 1 limit 1`)),
    "the chat shows the findings as the rubric check's; the agent gets them marked as data",
    obs.rubricCheck.replace(/\s+/g, " ").slice(0, 160),
  );
  obs.verdictSummaries = await page.$$eval("details > summary", (s) => s.map((e) => e.textContent.trim()).filter((t) => /rubric/.test(t)));
  for (const s of await page.$$("details > summary")) if (/rubric/.test(await s.evaluate((e) => e.textContent))) await s.click();
  obs.criteriaShown = await page.$$eval('ul[aria-label="Criteria"] li', (lis) => lis.map((li) => li.textContent.trim().slice(0, 120)));
  check(obs.verdictSummaries.length === tries && obs.criteriaShown.some((t) => t.startsWith("Not met:")), "the chat shows each verdict under the answer it graded, criterion by criterion", obs.verdictSummaries.join(" | "));

  // ---- notices: the one email of the firing, and what it leaves out
  const done = await mailTo(user.email, since, new RegExp(`${name} is done`));
  await new Promise((r) => setTimeout(r, 3000));
  const found = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${user.email}" subject:"${name}"`)}`, {
    headers: { Authorization: `Basic ${Buffer.from(`gen9:${kcEnv.MAILPIT_UI_PASSWORD ?? ""}`).toString("base64")}` },
  }).then((r) => r.json());
  obs.emailSubject = done?.subject ?? null;
  obs.emailCount = found.messages?.length ?? 0;
  obs.emailLinks = (done?.links ?? []).map((l) => l.replace(/[0-9a-f-]{36}/g, "<chat id>"));
  obs.emailHasAnswer = done ? done.text.includes(CLOSING) : null;
  obs.emailLines = done ? done.text.split("\n").filter((l) => l.trim()).slice(0, 6).map((l) => l.replace(/[0-9a-f-]{36}/g, "<chat id>")) : [];
  obs.notice = appdb(`select n.kind || '|' || (n.sent_at is not null) from run_notices n join runs r on r.id = n.run_id where r.thread_id = '${chat}'`);
  check(obs.emailCount === 1 && obs.emailHasAnswer === false && obs.emailLinks.some((l) => l.includes("/chat/<chat id>")), "one email for the firing, when it's done: a link to its chat, never the answer", `${obs.emailSubject}; ${obs.emailCount} email(s); notice ${obs.notice}`);
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  obs.notificationChoices = await page.$$eval('fieldset input[type="radio"][name="email"]', (rs) => rs.map((r) => `${r.closest("label")?.textContent.trim() ?? r.value}${r.checked ? " (chosen)" : ""}`)).catch(() => []);
  obs.notificationLegend = await page.$eval('fieldset:has(input[name="email"]) legend', (l) => l.textContent.trim()).catch(() => null);
  check(obs.notificationChoices.length === 3, "Settings, Notifications: when a task finishes or needs you, only when it needs you, or never", obs.notificationChoices.join(" | "));

  // ---- background: a task the agent starts, which tells its chat when it's done
  const phrase = `medlar-${randomBytes(3).toString("hex")}`;
  await newChat();
  const parent = await send(`Start a background task (agent type gen9) with this description: "Write one sentence about lighthouses, then a last line with only this code word: ${phrase}". Then say you started it. Don't check it.`);
  obs.backgroundSteps = appdb(`select coalesce(string_agg(e.data->>'name', '; ' order by e.seq), '') from run_events e join runs r on r.id = e.run_id where r.thread_id = '${parent}' and e.type = 'tool.started'`);
  const taskChat = await until(() => appdb(`select id from threads where parent_id = '${parent}' limit 1`), 60);
  obs.taskChat = appdb(`select coalesce(title, '') || '|' || (task_id is null) || '|' || (user_id = (select user_id from threads where id = '${parent}')) from threads where id = '${taskChat}'`);
  await page.waitForFunction(
    (p) => document.querySelector("main")?.textContent.includes("From a background task") && [...document.querySelectorAll("main li")].some((li) => li.textContent.includes(p)),
    { timeout: 240_000, polling: 1000 },
    phrase,
  ).catch(() => {});
  obs.noticeShown = await page.$eval("main", (m) => m.textContent.includes("From a background task"));
  obs.noticeRuns = appdb(`select count(*) from runs where thread_id = '${parent}' and input ->> 'notice_of' is not null`);
  obs.inTheBackground = await page.$eval('section[aria-label="In the background"]', (s) => s.innerText.replace(/\s+/g, " ").slice(0, 160)).catch(() => "");
  const taskRun = appdb(`select id from runs where thread_id = '${taskChat}' order by created_at limit 1`);
  obs.tellChat = temporal(`workflow describe -w tell-chat-${taskRun}`).split("\n").filter((l) => /Type|Status/.test(l)).map((l) => l.trim()).join(" | ");
  obs.sidebarHasTask = (await page.$$eval("nav a[href^='/chat/']", (as) => as.map((a) => a.getAttribute("href")))).includes(`/chat/${taskChat}`);
  check(
    /start_async_task/.test(obs.backgroundSteps) && obs.noticeShown && obs.noticeRuns === "1" && /TellChatWorkflow/.test(obs.tellChat) && !obs.sidebarHasTask,
    "the agent starts a task in a chat of its own, not in the sidebar; when it's done, TellChatWorkflow tells the chat in a turn of its own",
    `${obs.backgroundSteps}; ${obs.tellChat}; ${obs.inTheBackground}`,
  );
  await page.goto(`${APP}/chat/${taskChat}`, { waitUntil: "networkidle0" });
  const taskPage = await page.$eval("main", (m) => ({ composer: !!document.querySelector("#composer"), text: m.innerText.replace(/\s+/g, " ") }));
  obs.taskChatPage = { composer: taskPage.composer, note: taskPage.text.match(/A background task[^.]*\./)?.[0] ?? null };
  check(!taskPage.composer && !!obs.taskChatPage.note, "the task's chat shows its work and takes no messages", JSON.stringify(obs.taskChatPage));
  return obs;
}
