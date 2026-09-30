// Helpers the deeper batches share for driving a chat in the web app, as a person does: send a
// message and wait for the turn to end (or to wait for them), press a card's button, read a chat's
// tool steps from the run's events in Postgres. Nothing here prints what a chat says.
import { APP, appdb, readEnv, sh } from "./lib.mjs";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const RETRY_CARD = `section[aria-label="Gen9 couldn't finish"]`;
export const modelsdb = (q) => sh(`docker exec gen9-models-postgres-1 psql -U litellm -d litellm -tAc "${q.replaceAll('"', '\\"')}"`).out;
export const health = (name) => sh(`docker inspect -f '{{.State.Health.Status}}' ${name}`).out;

/** Polls `probe` once a second until it returns something truthy, for up to `seconds`. */
export async function until(probe, seconds) {
  for (let i = 0; i < seconds; i++) {
    const value = probe();
    if (value) return value;
    await sleep(1000);
  }
  return probe();
}

/** A chat's tool steps, in order: each tool's name, and for a subagent, which one. */
export const stepsOf = (thread) =>
  appdb(
    `select coalesce(string_agg(e.data->>'name' || coalesce(' ' || (e.data->'args'->>'subagent_type'), ''), '; ' order by e.seq), '')` +
      ` from run_events e join runs r on r.id = e.run_id where r.thread_id = '${thread}' and e.type = 'tool.started'`,
  );
/** Each run of a chat, oldest first, as status|attempts. */
export const runsOf = (thread) => appdb(`select coalesce(string_agg(status || '|' || attempts, ',' order by created_at), '') from runs where thread_id = '${thread}'`);
/** The event types of a chat's latest run, in order. */
export const eventsOf = (thread) =>
  appdb(`select coalesce(string_agg(type, ',' order by seq), '') from run_events where run_id = (select id from runs where thread_id = '${thread}' order by created_at desc limit 1)`);

/** The router's own API with its master key (gen9-models/.env; the key is never printed). */
const models = readEnv("gen9-models/.env");
export const router = (path, body) =>
  fetch(`http://127.0.0.1:${models.GEN9_MODELS_PORT || 19000}${path}`, {
    method: "POST",
    headers: { authorization: `Bearer ${models.LITELLM_MASTER_KEY}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export function chatHelpers(page) {
  const threadOf = () => page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
  const newChat = () => page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  /** Types a message (long ones set at once) and waits for the turn to end, or for `waitFor`. */
  const send = async (text, { waitFor = null, timeout = 300_000 } = {}) => {
    if (text.length > 400) {
      // A long message inserted in one go, as a paste does (CDP's Input.insertText: React sees it)
      await page.focus("#composer");
      await page.keyboard.sendCharacter(text);
    } else await page.type("#composer", text);
    await page.keyboard.press("Enter");
    await page.waitForSelector('button[aria-label="Stop"]', { timeout: 30_000 }).catch(() => {});
    await page.waitForFunction(
      (selector) => !document.querySelector('button[aria-label="Stop"]') || (selector && document.querySelector(selector)),
      { timeout, polling: 500 },
      waitFor,
    );
    return threadOf();
  };
  const button = async (scope, text) => {
    for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
    throw new Error(`no ${text} button in ${scope}`);
  };
  const lastAnswer = () => page.$$eval("ol[aria-live] > li", (lis) => lis.at(-1)?.textContent.trim().replace(/^Gen9 said: /, "") ?? "");
  return { threadOf, newChat, send, button, lastAnswer };
}
