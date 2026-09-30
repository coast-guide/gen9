// Admins need a second step (gen9-keycloak/config/configure.sh): after the password, Keycloak asks
// an admin for their authenticator app's code, or has one with no second step set an app up. The
// checks answer it here. The seeded admin's app has the secret GEN9_SEED_ADMIN_OTP_SECRET in
// gen9-keycloak/.env; a throwaway admin sets one up from the secret on Keycloak's page.
//
// Keycloak refuses a code used in the last 90 s (it keeps used codes by value) and takes the next
// window's code early (look-ahead 1): each sign-in takes a 30 s window no earlier one used, this
// one or the next, and waits only when both are taken. The windows used are kept in a file, so the
// checks of one `make e2e` don't reuse one. No dependencies: gen9-learn's verifier imports it too.
import { createHash, createHmac } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
// The realm's OTP policy: Keycloak's default (TOTP, HmacSHA1, 6 digits, 30 s), which Gen9 keeps
const PERIOD = 30;
const DIGITS = 6;
const USED = join(tmpdir(), "gen9-checks-otp-windows.json");

/** RFC 6238 as Keycloak computes it: the secret's own bytes are the HMAC key. */
export function totp(secret, window = Math.floor(Date.now() / 1000 / PERIOD)) {
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(window));
  const hash = createHmac("sha1", Buffer.from(secret, "utf8")).update(message).digest();
  const offset = hash[hash.length - 1] & 0xf;
  return String((hash.readUInt32BE(offset) & 0x7fffffff) % 10 ** DIGITS).padStart(DIGITS, "0");
}

/** A window for `secret` no earlier sign-in used: now, or the next one, else wait for one. */
export async function freshWindow(secret) {
  const key = createHash("sha256").update(secret).digest("hex").slice(0, 16);
  for (;;) {
    let used = {};
    try {
      used = JSON.parse(readFileSync(USED, "utf8"));
    } catch {
      // none used yet, or a file from elsewhere: start over
    }
    const now = Math.floor(Date.now() / 1000 / PERIOD);
    const mine = (used[key] ?? []).filter((w) => w >= now - 3);
    const window = [now, now + 1].find((w) => !mine.includes(w));
    if (window !== undefined) {
      used[key] = [...mine, window];
      writeFileSync(USED, JSON.stringify(used), { mode: 0o600 });
      return window;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

/** The seeded admin's authenticator secret, from gen9-keycloak/.env. */
export function seededAdminSecret() {
  const file = `${ROOT}gen9-keycloak/.env`;
  const line = existsSync(file) ? readFileSync(file, "utf8").split("\n").find((l) => l.startsWith("GEN9_SEED_ADMIN_OTP_SECRET=")) : undefined;
  if (!line) throw new Error("No GEN9_SEED_ADMIN_OTP_SECRET in gen9-keycloak/.env (make setup STACKS=keycloak adds it)");
  return line.slice(line.indexOf("=") + 1);
}

const submitted = (page, action) => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 30_000 }), action]);

/**
 * After the password, answer the second step if Keycloak asks for one: the authenticator code
 * (from `secret`, the seeded admin's unless given), or setting up an app for an admin with none.
 * Resolves to what it did: { did: "code" }, { did: "set up", secret } (the new app's secret), or
 * null when nothing was asked.
 */
export async function secondStep(page, secret) {
  if (await page.$("#totpSecret")) {
    const fresh = await page.$eval("#totpSecret", (input) => input.value);
    await page.type("#totp", totp(fresh, await freshWindow(fresh)));
    await submitted(page, page.$eval("#totp", (input) => input.form.requestSubmit()));
    return { did: "set up", secret: fresh };
  }
  if (!(await page.$("#otp"))) return null;
  const key = secret ?? seededAdminSecret();
  // A code refused (one used elsewhere in the same windows): once more, with the next free window
  for (let attempt = 0; attempt < 2 && (await page.$("#otp")); attempt++) {
    await page.$eval("#otp", (input) => (input.value = ""));
    await page.type("#otp", totp(key, await freshWindow(key)));
    await submitted(page, page.click("#kc-login"));
  }
  return { did: "code" };
}
