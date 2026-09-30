// Batch 9, deeper, before b6 deletes the account: the person's data as one ZIP (GDPR Art. 20). Settings'
// "Download a copy" streams it from gen9-agent through gen9-ui; the batch unzips it and checks each
// part against what the person gave Gen9 in the earlier parts (their chats, the attached file, the
// memory, the task, the connectors), that no secret's value is in it, that another browser without a
// session gets nothing, and that the export is in the audit record. No model call.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { APP, appdb, check, navigation, nextWindow, otpPolicy, signInWithPassword, totp } from "../lib.mjs";

export default async function exported(ctx) {
  const { browser, page, user } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  const dir = mkdtempSync(join(tmpdir(), "gen9-learn-export-"));
  const downloads = join(dir, "downloads");
  const since = new Date().toISOString();
  try {
    // Signed in (b5 and b5x leave the web session as it was; sign in again if it's gone)
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    if (!page.url().startsWith(`${APP}/settings`)) {
      await signInWithPassword(page, user.email, user.password);
      user.otpCounter = await nextWindow(policy, user.otpCounter);
      await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
      await navigation(page, page.click("#kc-login"));
      await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    }
    obs.row = await page.evaluate(() => {
      const label = [...document.querySelectorAll("p")].find((p) => p.textContent.trim() === "Download a copy");
      return label?.closest("div")?.parentElement?.innerText.replace(/\s+/g, " ") ?? null;
    });
    const cdp = await page.createCDPSession();
    await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads, eventsEnabled: true });
    const finished = new Promise((resolve) => cdp.on("Browser.downloadProgress", (e) => e.state === "completed" && resolve(e)));
    const started = Date.now();
    await page.click('a[href="/api/export"]');
    await Promise.race([finished, new Promise((_, reject) => setTimeout(() => reject(new Error("no download in 60 s")), 60_000))]);
    obs.ms = Date.now() - started;
    const [zip] = readdirSync(downloads).filter((f) => f.endsWith(".zip"));
    obs.zipName = zip?.replace(/\d{4}-\d{2}-\d{2}/, "<date>");
    const listed = execFileSync("unzip", ["-Z1", join(downloads, zip)], { encoding: "utf8" }).trim().split("\n");
    obs.parts = listed.filter((f) => !f.startsWith("files/"));
    obs.files = listed.filter((f) => f.startsWith("files/")).map((f) => f.replace(/[0-9a-f-]{36}/g, "<chat id>"));
    const out = join(dir, "unzipped");
    execFileSync("unzip", ["-q", join(downloads, zip), "-d", out]);
    const read = (name) => readFileSync(join(out, name), "utf8");
    const chats = JSON.parse(read("conversations.json"));
    const account = JSON.parse(read("account.json"));
    obs.account = Object.keys(account).join(", ");
    obs.conversations = { count: chats.length, keys: Object.keys(chats[0] ?? {}).join(", "), firstChatThere: chats.some((c) => c.id === ctx.threadId) };
    obs.memory = read("memory.md").split("\n").filter(Boolean).length;
    obs.tasks = JSON.parse(read("tasks.json")).map((t) => t.name);
    obs.readme = read("README.txt").split("\n").slice(0, 3).join(" ");
    const everything = listed.filter((f) => !f.endsWith("/")).map((f) => readFileSync(join(out, f), "utf8")).join("\n");
    obs.sealedValueIn = /[A-Za-z0-9_-]+:[A-Za-z0-9+/]{40,}={0,2}/.test(read("connectors.json")) || /sealed/.test(read("environment-secrets.json"));
    obs.noTokens = !/access_token|refresh_token|Bearer /.test(everything);
    check(
      zip && ["README.txt", "account.json", "conversations.json", "memory.md", "tasks.json", "connectors.json", "environment-secrets.json", "plugins.json", "usage.json", "audit.json", "apps.json", "sign-ins.json"].every((f) => listed.includes(f)),
      "Download a copy gives a dated ZIP: a README and each part as JSON or Markdown, and the chats' files",
      `${obs.zipName}, ${obs.ms} ms; ${obs.parts.join(", ")}; ${obs.files.join(", ")}`,
    );
    check(
      obs.conversations.firstChatThere && obs.tasks.includes("Morning brief") && obs.noTokens && !obs.sealedValueIn,
      "it holds what the person gave Gen9 (their chats, the task, the memory) and no token or sealed value",
      `${obs.conversations.count} chats {${obs.conversations.keys}}; tasks ${obs.tasks.join(", ")}; memory ${obs.memory} line(s)`,
    );
    // What the sign-in service keeps about them: the apps they allowed (Gen9 CLI, from b5), and
    // their sign-in records, this one's own among them (gen9-learn.md, M9, F14)
    const apps = JSON.parse(read("apps.json"));
    const signIns = JSON.parse(read("sign-ins.json"));
    obs.apps = Array.isArray(apps) ? apps.map((a) => a.app).join(", ") : JSON.stringify(apps);
    obs.signIns = Array.isArray(signIns) ? `${signIns.length} records: ${[...new Set(signIns.map((e) => e.type))].slice(0, 5).join(", ")}; fields ${Object.keys(signIns[0] ?? {}).join(", ")}` : JSON.stringify(signIns);
    check(
      Array.isArray(apps) && apps.some((a) => a.app === "gen9-cli") && Array.isArray(signIns) && signIns.some((e) => e.type === "LOGIN" && e.app === "gen9-ui" && e.ip),
      "it holds what the sign-in service keeps about them: the apps they allowed, and their sign-in records",
      `apps: ${obs.apps}; sign-ins: ${obs.signIns}`,
    );
    // Without a session: nothing
    const visitor = await browser.createBrowserContext();
    const other = await visitor.newPage();
    const bare = await other.goto(`${APP}/api/export`, { waitUntil: "load" });
    obs.withoutSession = `${bare.status()} ${other.url().replace(APP, "").slice(0, 60)}`;
    await visitor.close();
    obs.audit = appdb(`select string_agg(action || ' ' || outcome, ', ') from audit_events where actor = '${user.sub}' and at >= '${since}'`);
    check(!/^200 \/api\/export$/.test(obs.withoutSession) && /export/.test(obs.audit), "without a session, no export; the export is in the audit record", `${obs.withoutSession}; ${obs.audit}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  return obs;
}
