// Re-runs gen9-learn's story against the running stacks, as one throwaway user from birth to
// deletion, and writes what each batch observed to out/<batch>.json (sanitized).
//   node run.mjs                 every batch
//   node run.mjs b1 b2           some batches (later ones need the earlier ones' user)
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import puppeteer from "puppeteer-core";
import { admin, CHROME, check, failed, recorder, sanitizeUrl, signInWithPassword } from "./lib.mjs";

const BATCHES = {
  b1: (await import("./batches/b1-birth.mjs")).default,
  b1d: (await import("./batches/b1d-told.mjs")).default,
  b2: (await import("./batches/b2-using.mjs")).default,
  b2d: (await import("./batches/b2d-deeper.mjs")).default,
  b2w: (await import("./batches/b2w-work.mjs")).default,
  b2wd: (await import("./batches/b2wd-work-deeper.mjs")).default,
  b2x: (await import("./batches/b2x-more.mjs")).default,
  b2xd: (await import("./batches/b2xd-more-deeper.mjs")).default,
  commands: (await import("./batches/commands.mjs")).default,
  b3: (await import("./batches/b3-securing.mjs")).default,
  b4: (await import("./batches/b4-sessions.mjs")).default,
  b4d: (await import("./batches/b4d-admin.mjs")).default,
  b5: (await import("./batches/b5-cli.mjs")).default,
  b5x: (await import("./batches/b5x-others.mjs")).default,
  b5xd: (await import("./batches/b5xd-agents-deeper.mjs")).default,
  // b6d before b6: the export needs the account b6 deletes
  b6d: (await import("./batches/b6d-export.mjs")).default,
  b6: (await import("./batches/b6-death.mjs")).default,
  b7: (await import("./batches/b7-running.mjs")).default,
  b7d: (await import("./batches/b7d-inside.mjs")).default,
};
const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(BATCHES);
const out = new URL("./out/", import.meta.url).pathname;
mkdirSync(out, { recursive: true });

// host.docker.internal is how containers reach the test servers on this machine; a connector's
// sign-in sends the browser there too (b2xd), so Chrome resolves it to this machine
const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
  args: ["--host-resolver-rules=MAP host.docker.internal 127.0.0.1"],
});
const page = await browser.newPage();
// REUSE=1 signs in again as the user a KEEP=1 run left (out/user.json, git-ignored; its password is
// typed by Puppeteer, never printed), to re-run later batches without b1 and b2
const USER_FILE = `${out}user.json`;
const reused = process.env.REUSE ? JSON.parse(readFileSync(USER_FILE, "utf8")) : null;
const ctx = {
  browser,
  page,
  rec: await recorder(page),
  user: reused?.user ?? { email: `trace-${Date.now()}@gen9.test`, password: `learn-${randomBytes(12).toString("hex")}` },
  threadId: reused?.threadId,
  startedAt: Date.now(),
};
try {
  if (reused) {
    await signInWithPassword(page, ctx.user.email, ctx.user.password);
    console.log(`      (signed in again as ${ctx.user.email})`);
  }
  for (const name of wanted) {
    console.log(`\n== ${name}`);
    const obs = await BATCHES[name](ctx);
    writeFileSync(`${out}${name}.json`, JSON.stringify(obs, null, 2));
  }
} catch (error) {
  // Messages only: an exception's text can quote page content, never dump more than its first line
  check(false, "unexpected error", String(error.message).split("\n")[0].slice(0, 160));
  // Where it stopped, for whoever debugs it: the address (sanitized) and a screenshot in out/,
  // which is never committed. Password fields show as dots
  await page.screenshot({ path: `${out}failure.png` }).catch(() => {});
  console.log(`      (stopped on ${sanitizeUrl(page.url()).slice(0, 160)}; out/failure.png)`);
} finally {
  writeFileSync(`${out}network.json`, JSON.stringify(ctx.rec.log, null, 2));
  if (process.env.KEEP) writeFileSync(USER_FILE, JSON.stringify({ user: ctx.user, threadId: ctx.threadId }), { mode: 0o600 });
  await browser.close();
  // The tracing user goes, whatever happened; gen9-agent's sweep then removes its Gen9 data
  if (!process.env.KEEP) {
    const [left] = await admin(`/users?email=${encodeURIComponent(ctx.user.email)}&exact=true`).catch(() => []);
    if (left) await admin(`/users/${left.id}`, { method: "DELETE" }).then(() => console.log(`      (deleted ${ctx.user.email} from Keycloak)`));
  }
}
console.log(failed() ? `\n${failed()} check(s) failed` : "\nall checks passed");
process.exit(failed() ? 1 : 0);
