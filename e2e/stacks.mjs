// Every call between stacks, end to end in real Chrome, the way a user sets them off:
//   0. the home page, signed out       what Gen9 is in one screen, and its two ways in (gen9-ui alone)
//   1. sign in with a password         gen9-ui <-> gen9-keycloak (code exchanged on gen9-keycloak:8080)
//   2. ask something in a new chat     gen9-ui -> gen9-agent -> gen9-keycloak (JWKS), gen9-postgres,
//                                      the model; the answer streams back, and is still there on reload
//   3. the run is traced in Langfuse   gen9-agent -> gen9-langfuse
//   4. an admin ends the user's        gen9-keycloak -> gen9-ui back-channel logout: the next page
//      sessions in Keycloak            load is signed out, well before the access token expires
// Needs every stack up (make up) and a provider key in gen9-models/.env.
import { existsSync, readFileSync } from "node:fs";
import { launch } from "./browser.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const readEnv = (file) =>
  existsSync(file)
    ? Object.fromEntries(
        readFileSync(file, "utf8")
          .split("\n")
          .filter((line) => /^[A-Z0-9_]+=/.test(line))
          .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
      )
    : {};
const env = readEnv(`${ROOT}gen9-keycloak/.env`);
// Langfuse keys as gen9-agent gets them: langfuse.local.env wins over .env
const agentEnv = { ...readEnv(`${ROOT}gen9-agent/.env`), ...readEnv(`${ROOT}gen9-agent/langfuse.local.env`) };
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const LANGFUSE = process.env.LANGFUSE_URL ?? "http://localhost:13000";
const EMAIL = env.GEN9_SEED_ADMIN_EMAIL;
const PASSWORD = env.GEN9_SEED_ADMIN_PASSWORD;
const QUESTION = "Reply with one word: pong";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

// Keycloak Admin API as the bootstrap admin: the user's id, sessions, and ending them
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

// Observations Langfuse has for the user since the check started (v2 API, as gen9-agent's erasure uses)
async function traced(userId, since) {
  const query = new URLSearchParams({ userId, fromStartTime: since.toISOString(), fields: "core", limit: "50" });
  const response = await fetch(`${LANGFUSE}/api/public/v2/observations?${query}`, {
    headers: {
      Authorization: `Basic ${Buffer.from(`${agentEnv.LANGFUSE_PUBLIC_KEY}:${agentEnv.LANGFUSE_SECRET_KEY}`).toString("base64")}`,
    },
  });
  if (!response.ok) throw new Error(`Langfuse observations: ${response.status}`);
  return (await response.json()).data;
}

const [user] = await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`);
const startedAt = new Date();

const browser = await launch({
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
});
const page = await browser.newPage();
const navigation = (action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), action]);
const inApp = () => page.url().startsWith(`${APP}/`) && !page.url().includes("/signed-out");

try {
  // The home page, signed out: its words and its two actions (docs/design/screens/home.md)
  await page.goto(`${APP}/`, { waitUntil: "networkidle0" });
  const home = await page.evaluate(() => {
    const shown = (el) => el.getClientRects().length > 0;
    return {
      headline: document.querySelector("h1")?.textContent.trim(),
      invited: [...document.querySelectorAll("p")].some((p) => shown(p) && p.textContent.trim() === "Give it something to do."),
      example: !!document.querySelector('section[aria-label="Example task"] figure'),
      actions: [...document.querySelectorAll("a")].filter(shown).map((a) => `${a.textContent.trim()} ${a.getAttribute("href")}`),
    };
  });
  check(
    home.headline === "Get any task done with autonomous agents." &&
      home.invited &&
      home.example &&
      home.actions.join("; ") === "Sign in /auth/login; Create an account /auth/login?intent=signup",
    "the home page says what Gen9 is, with one example and its two ways in",
    `${home.headline} [${home.actions.join("; ")}]`,
  );

  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", EMAIL);
  await page.type("#password", PASSWORD);
  await navigation(page.click("#kc-login"));
  if (!check(inApp(), "password sign-in (gen9-ui <-> gen9-keycloak)", page.url())) throw new Error("cannot continue");

  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  await page.type("#composer", QUESTION);
  await page.click('button[aria-label="Send"]');
  // Done when the Stop button is gone: the last item is then the answer, or the question again if
  // the run failed (the empty answer is removed and a toast says why)
  const outcome = await page
    .waitForFunction(
      () => {
        const items = [...document.querySelectorAll("ol[aria-live] > li")];
        const last = items.at(-1);
        if (document.querySelector('button[aria-label="Stop"]') || !last || last.querySelector(".animate-pulse")) return false;
        const toast = document.querySelector("[data-sonner-toast]")?.textContent;
        return last.classList.contains("ml-auto") ? { error: toast || "no answer" } : { answer: last.textContent.trim() };
      },
      { timeout: 180_000, polling: 500 },
    )
    .then((handle) => handle.jsonValue());
  check(!!outcome.answer, "the agent answered (gen9-ui -> gen9-agent -> model)", outcome.answer?.slice(0, 80) ?? outcome.error);

  const thread = page.url();
  check(/\/chat\/[0-9a-f-]{36}$/.test(thread), "the chat got its own thread", thread);
  await page.goto(thread, { waitUntil: "networkidle0" });
  const saved = await page.$$eval("ol[aria-live] > li", (items) => items.map((li) => li.textContent.trim().replace(/^(You|Gen9) said: /, "")));
  check(saved.length >= 2 && saved[0] === QUESTION, "on reload the chat is still there (gen9-agent -> gen9-postgres)", `${saved.length} messages`);

  // Ingestion is asynchronous (the worker processes a queue): allow a minute
  let observations = [];
  for (let i = 0; i < 30 && observations.length === 0; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    observations = await traced(user.id, startedAt);
  }
  check(observations.length > 0, "the run is traced in Langfuse under the user (gen9-agent -> gen9-langfuse)", `${observations.length} observations`);

  // The chat goes, as the admin deletes it from the web app, so the seeded admin's sidebar and
  // searches don't fill up with one "pong" chat per run (27 were found)
  const clickText = async (selector, text) => {
    for (const el of await page.$$(selector)) if ((await el.evaluate((e) => e.textContent.trim())) === text) return el.click();
  };
  // Renamed first (Chat options, Rename): typed into the field that takes the title's place, kept
  // after a reload, in the title bar and the sidebar
  const name = `Stacks check ${Date.now()}`;
  await page.click('button[aria-label="Chat options"]');
  await page.waitForSelector("[role=menuitem]");
  await clickText("[role=menuitem]", "Rename");
  await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Chat name", { timeout: 5_000 }).catch(() => {});
  await page.keyboard.type(name);
  // The title bar shows the name at once; the reload waits for the save itself, which it would
  // otherwise overtake (it did)
  const renaming = page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().includes("/api/threads/"), { timeout: 10_000 }).catch(() => null);
  await page.keyboard.press("Enter");
  const save = await renaming;
  await page.reload({ waitUntil: "networkidle0" });
  const renamed = await page.evaluate(() => ({
    heading: document.querySelector("main h1")?.textContent.trim(),
    sidebar: [...document.querySelectorAll('a[aria-current="page"]')].map((a) => a.textContent.trim()),
  }));
  check(save?.status() === 200 && renamed.heading === name && renamed.sidebar.includes(name), "the chat is renamed (Chat options, Rename), after a reload too", `${save?.status() ?? "no save"} ${JSON.stringify(renamed)}`);

  await page.click('button[aria-label="Chat options"]');
  await page.waitForSelector("[role=menuitem]");
  await clickText("[role=menuitem]", "Delete chat");
  await page.waitForSelector("[role=alertdialog]");
  await clickText("[role=alertdialog] button", "Delete chat");
  const deleted = await page.waitForFunction(() => location.pathname === "/chat", { timeout: 15_000 }).then(() => true).catch(() => false);
  check(deleted, "the check's chat is deleted (Chat options, Delete chat)", page.url());

  const sessions = await admin(`/users/${user.id}/sessions`);
  check(sessions.length > 0, "Keycloak has the user's session", `${sessions.length}`);
  await admin(`/users/${user.id}/logout`, { method: "POST" });
  check((await admin(`/users/${user.id}/sessions`)).length === 0, "the admin ended every session in Keycloak");
  // Without back-channel logout, gen9-ui would keep serving the chat until the access token expires
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  check(!inApp(), "gen9-ui dropped the session: signed out (gen9-keycloak -> gen9-ui back-channel logout)", page.url());
} catch (error) {
  check(false, "unexpected error", error.message);
} finally {
  await browser.close();
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
