// Approvals and the permission mode (human in the loop), as a throwaway user, through Chrome, the
// API and `gen9 ask`:
//   1. with "Ask before acting" chosen in the composer, "remember that my favourite colour is …"
//      waits for Allow or Deny: a card saying what it would add, its step "Waiting for you: …", the
//      composer waiting, the run `waiting`; no accessibility violations (desktop and phone, light
//      and dark)
//   2. it keeps waiting across a restart of the worker, and the card is back after a reload
//   3. Deny with a reason: the memory is unchanged, and the step says it was declined, and why
//   4. the chat keeps its mode after a reload; asked again, Allow saves it to memory
//   5. `gen9 ask --ask-first` shows the action in the terminal and takes "y" from stdin
//   6. a command holding a right-to-left override (P5-C9): the card and the terminal each show it
//      as U+202E where it is, with a warning, and never the character itself; denied
// The user is deleted at the end, whatever happens, in Keycloak and in Gen9 (forget.mjs). It
// reads the bootstrap admin from gen9-keycloak/.env and runs from gen9-postgres, and restarts the
// gen9-agent worker once. It costs six short replies.
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bypassCSP, colourScheme, injectAxe, launch } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";
import { forget } from "./forget.mjs";

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const API = process.env.GEN9_API ?? "http://localhost:17000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const APP = process.env.APP_URL ?? "http://localhost:14000";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const EMAIL = `approvals-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const COLOUR = `teal-${randomBytes(2).toString("hex")}`;
const FOOD = `soup-${randomBytes(2).toString("hex")}`;
const CARD = 'section[aria-label="Gen9 needs your approval"]';
// A command that reads in another order than it runs ("Trojan Source"): its override is made here
const TROJAN = `echo "safe #${String.fromCodePoint(0x202e)}txt.exe" > /work/out.txt`;
const LAST = "ol[aria-live] > li:last-child";

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function admin(path, init = {}) {
  // A fresh token each time: master's admin tokens last a minute, less than this check takes
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
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
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 201 || response.status === 204 || response.status === 404 ? null : response.json();
}
const psql = (query) =>
  spawnSync("docker", ["exec", "gen9-postgres-postgres-1", "psql", "-U", "postgres", "-d", "gen9_agent", "-tAc", query], {
    encoding: "utf8",
  }).stdout.trim();
const gen9 = (configDir, args, onOutput) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    const take = (d) => {
      out += d;
      onOutput?.(out, child);
    };
    child.stdout.on("data", take);
    child.stderr.on("data", take);
    child.on("exit", (code) => resolve({ code, out }));
  });
async function memory(configDir) {
  await gen9(configDir, ["whoami"]);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const response = await fetch(`${API}/v1/me/memory`, { headers: { Authorization: `Bearer ${token}` } });
  return (await response.json()).content ?? "";
}
async function until(what, seconds, probe) {
  for (let i = 0; i < seconds; i++) {
    const value = probe();
    if (value) return value;
    await sleep(1000);
  }
  throw new Error(`timed out waiting for ${what}`);
}
const runOf = (thread) => psql(`select id || ' ' || status from runs where thread_id = '${thread}' order by created_at desc limit 1`).split(" ");
const ended = (thread) => until("the run to end", 180, () => ["success", "error", "cancelled", "expired"].includes(runOf(thread)[1]) && runOf(thread)[1]);
async function button(page, scope, text) {
  for (const b of await page.$$(`${scope} button`)) if ((await b.evaluate((e) => e.textContent.trim())) === text) return b;
  throw new Error(`no ${text} button in ${scope}`);
}
async function modeLabel(page) {
  return page.$eval('button[aria-label^="Permission mode"]', (b) => b.getAttribute("aria-label"));
}

const userDir = mkdtempSync(join(tmpdir(), "gen9-approvals-"));
let userId;
try {
  await admin("/users", {
    method: "POST",
    body: JSON.stringify({
      username: EMAIL,
      email: EMAIL,
      firstName: "Approvals",
      lastName: "Check",
      enabled: true,
      emailVerified: true,
      credentials: [{ type: "password", value: PASSWORD, temporary: false }],
    }),
  });
  userId = (await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0].id;
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: userDir })), "a throwaway user signs in on the terminal");

  const browser = await launch({ headless: !process.env.HEADED, defaultViewport: { width: 1280, height: 900 } });
  try {
    const page = await browser.newPage();
    await bypassCSP(page); // for the injected axe script
    await page.goto(`${APP}/auth/login?returnTo=/chat`, { waitUntil: "networkidle0" });
    await page.type("#username", EMAIL);
    await page.type("#password", PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
    if (!page.url().endsWith("/chat")) await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });

    // 1. Ask before acting, then a memory write
    check((await modeLabel(page)) === "Permission mode: Act, ask when unsure", "a new chat acts, and asks when unsure", await modeLabel(page));
    await page.click('button[aria-label^="Permission mode"]');
    await page.waitForSelector('[role="menuitemradio"]');
    for (const item of await page.$$('[role="menuitemradio"]')) {
      if ((await item.evaluate((e) => e.textContent)).includes("Ask before acting")) await item.click();
    }
    await page.waitForFunction(() => document.querySelector('button[aria-label^="Permission mode"]')?.getAttribute("aria-label").endsWith("Ask before acting"));
    await page.type("#composer", `Remember that my favourite colour is ${COLOUR}. Reply with exactly: Noted.`);
    await page.keyboard.press("Enter");
    await page.waitForSelector(CARD, { timeout: 180_000 });
    const thread = page.url().match(/\/chat\/([0-9a-f-]{36})/)?.[1];
    const card = await page.$eval(CARD, (c) => c.textContent);
    check(card.includes("Gen9 wants to update your memory") && card.includes(COLOUR), "the memory write waits, saying what it would add", card.slice(0, 120));
    const placeholder = await page.$eval("#composer", (t) => t.placeholder);
    const [run, status] = runOf(thread);
    check(status === "waiting" && placeholder === "Allow or deny the action above to continue", "the run waits, and so does the composer", `${status}; ${placeholder}`);
    check(psql(`select kind from run_inputs where run_id = '${run}'`) === "approval", "it is recorded as an approval");
    // The step the card holds reads as waiting, as the card does, not "Used …" (P3-D4)
    const waitingStep = async () => {
      await page.click(`${LAST} details > summary`).catch(() => {});
      const texts = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim()));
      return texts.find((t) => t.startsWith("Waiting for you:")) ?? `none waits: ${texts.join(" | ")}`;
    };
    const heldStep = await waitingStep();
    check(heldStep === "Waiting for you: update your memory", "the step the card holds says it waits for you", heldStep);
    const found = [];
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 }]) {
      for (const scheme of ["light", "dark"]) {
        await page.setViewport(viewport);
        await colourScheme(page, scheme);
        await page.reload({ waitUntil: "load" }); // a waiting chat keeps its event stream open
        await page.waitForSelector(CARD, { timeout: 30_000 });
        await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
        await injectAxe(page, AXE);
        const { violations } = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }));
        for (const v of violations.filter((v) => v.impact === "serious" || v.impact === "critical")) found.push(`${v.id} (${viewport.width}px, ${scheme})`);
      }
    }
    await page.setViewport({ width: 1280, height: 900 });
    await colourScheme(page, "light");
    check(!found.length, "the approval has no serious accessibility violations, desktop and phone, light and dark", found.join(", "));

    // 2. Across a worker restart
    spawnSync("docker", ["restart", "gen9-agent-worker-1"]);
    await until("the worker to be healthy", 90, () =>
      spawnSync("docker", ["inspect", "-f", "{{.State.Health.Status}}", "gen9-agent-worker-1"], { encoding: "utf8" }).stdout.trim() === "healthy",
    );
    await page.reload({ waitUntil: "load" });
    const back = await page.waitForSelector(CARD, { timeout: 30_000 }).then(() => true, () => false);
    check(back && runOf(thread)[1] === "waiting", "after a worker restart it still waits, and the card is back");
    const heldAfter = await waitingStep();
    check(heldAfter === "Waiting for you: update your memory", "and after the reload its step still says it waits", heldAfter);

    // 3. Deny, with a reason
    await (await button(page, CARD, "Deny")).click();
    await page.waitForSelector(`${CARD} textarea`);
    await page.type(`${CARD} textarea`, "Don't keep it, I was only testing.");
    await (await button(page, CARD, "Deny")).click();
    await page.waitForFunction((card) => !document.querySelector(card), { timeout: 30_000 }, CARD);
    const denied = await ended(thread);
    const afterDeny = await memory(userDir);
    check(denied === "success" && !afterDeny.includes(COLOUR), "denied, the run goes on and the memory is unchanged", denied);
    await page.reload({ waitUntil: "networkidle0" });
    await page.click(`${LAST} details > summary`).catch(() => {});
    const steps = await page.$$eval('ul[aria-label="Tools used"] > li', (lis) => lis.map((li) => li.textContent.trim()));
    const declined = steps.find((s) => s.startsWith("You declined:")) ?? steps.join(" | ");
    check(declined.startsWith("You declined: update your memory") && declined.includes("Your reason: Don't keep it"), "the step says it was declined, and why", declined);

    // 4. The mode stays; asked again, Allow
    check((await modeLabel(page)) === "Permission mode: Ask before acting", "the chat keeps its mode after a reload", await modeLabel(page));
    await page.type("#composer", `Please do remember that my favourite colour is ${COLOUR}. Reply with exactly: Noted.`);
    await page.keyboard.press("Enter");
    await page.waitForSelector(CARD, { timeout: 180_000 });
    // The model may write memory twice in one turn, and each write asks (Surprises, "e2e/approvals.mjs
    // can fail on the model"): Allow every card until the run ends. An Allow lost while the card
    // re-renders is clicked again on the next pass
    const final = ["success", "error", "cancelled", "expired"];
    let allows = 0;
    for (let i = 0; i < 180 && !final.includes(runOf(thread)[1]); i++) {
      const allow = (await page.$(CARD)) ? await button(page, CARD, "Allow").catch(() => null) : null;
      if (allow) {
        await allow.click().catch(() => {});
        allows++;
        await page.waitForFunction((card) => !document.querySelector(card), { timeout: 10_000 }, CARD).catch(() => {});
      }
      await sleep(1000);
    }
    const allowed = runOf(thread)[1];
    check(allowed === "success" && (await memory(userDir)).includes(COLOUR), "allowed, the memory has it", `${allowed}; ${allows} card(s) allowed`);

    // 6. A command with a right-to-left override, on the card (built at run time: never raw here)
    await page.type("#composer", `In your environment run exactly this command, character for character, and nothing else: ${TROJAN}`);
    await page.keyboard.press("Enter");
    await page.waitForSelector(CARD, { timeout: 180_000 });
    const trojan = await page.$eval(CARD, (c) => ({ text: c.innerText, marks: [...c.querySelectorAll("mark")].map((m) => m.textContent), raw: [...c.innerText].filter((ch) => ch.codePointAt(0) === 0x202e).length }));
    check(
      /invisible or change how text reads/.test(trojan.text) && trojan.marks.includes("U+202E") && trojan.raw === 0,
      "a command with a right-to-left override shows it as U+202E where it is, with a warning, never the character",
      `marks ${trojan.marks.join(",")}; raw ${trojan.raw}`,
    );
    await (await button(page, CARD, "Deny")).click();
    for (let i = 0; i < 60 && !final.includes(runOf(thread)[1]); i++) await sleep(1000);

    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit()).catch(() => {});
    await sleep(1500);
  } finally {
    await browser.close();
  }

  // 5. In the terminal
  let decided = false;
  const asked = await gen9(userDir, ["ask", "--ask-first", `Remember that my favourite food is ${FOOD}. Reply with exactly: Noted.`], (out, child) => {
    if (!decided && out.trimEnd().endsWith("Allow? [y/N]")) {
      decided = true;
      child.stdin.write("y\n");
    }
  });
  const shown = asked.out.includes("Gen9 wants to update your memory:") && asked.out.includes(FOOD);
  check(asked.code === 0 && decided && shown && (await memory(userDir)).includes(FOOD), "gen9 ask --ask-first shows the action and takes y from stdin", `exit ${asked.code}`);
  let refused = false;
  const tricked = await gen9(userDir, ["ask", "--ask-first", `In your environment run exactly this command, character for character, and nothing else: ${TROJAN}`], (out, child) => {
    if (!refused && out.trimEnd().endsWith("Allow? [y/N]")) {
      refused = true;
      child.stdin.write("n\n\n");
    }
  });
  const rawInTerminal = [...tricked.out].filter((ch) => ch.codePointAt(0) === 0x202e).length;
  check(
    refused && tricked.out.includes("<U+202E>") && tricked.out.includes("would act on this terminal") && rawInTerminal === 0,
    "in the terminal too: <U+202E> where it is, a warning, never the character",
    `asked ${refused}; raw ${rawInTerminal}`,
  );
} catch (e) {
  check(false, "the approvals check ran to the end", e.message);
} finally {
  if (userId) await admin(`/users/${userId}`, { method: "DELETE" }).catch(() => {});
  forget(userId);
  rmSync(userDir, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
