// Keycloak's pages by keyboard alone, in real Chrome: nothing is clicked; only Tab, Enter and Space
// are pressed, and text is typed into whatever has focus (WCAG 2.2, 2.1.1 Keyboard, 2.4.3 Focus
// Order):
//   1. a throwaway user (Admin API), asked to set up an authenticator app and recovery codes
//   2. sign in: focus starts in Email, Tab reaches Password, Enter signs in
//   3. the authenticator app: its code field by Tab, Enter saves it
//   4. the recovery codes: "I've saved these codes" by Tab and Space, Enter saves them
//   5. signed out, the password again: focus lands in the code field; a wrong code is refused at
//      the field (aria-invalid, its message named by aria-describedby); the right one signs in
//   6. signed out, the password again: "Try another way" by Tab and Enter, the recovery code, Enter
// At each, the fields take a paste, and Email and Password name themselves to password managers
// (WCAG 2.2, 3.3.8 Accessible Authentication)
// It prints each page's Tab order. The user is deleted at the end, whatever happens. It reads the
// bootstrap admin from gen9-keycloak/.env; no model is called.
import { createHmac, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { chromeOnly, launch, TOOLBAR_FOCUS } from "./browser.mjs";

if (chromeOnly("the keyboard walk", TOOLBAR_FOCUS)) process.exit(0);

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const EMAIL = `keyboard-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;

let failures = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures++;
  return ok;
}

// Keycloak Admin API as the bootstrap admin (as recovery.mjs)
async function admin(path, init = {}) {
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
    headers: { Authorization: `Bearer ${admin.token}`, "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`Keycloak ${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.status === 201 || response.status === 204 ? null : response.json();
}

// RFC 6238 with the realm's policy (as recovery.mjs)
const realm = await admin("");
const totp = (secret, counter) => {
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const algorithm = realm.otpPolicyAlgorithm.replace("Hmac", "").toLowerCase();
  const hash = createHmac(algorithm, Buffer.from(secret, "utf8")).update(message).digest();
  const offset = hash[hash.length - 1] & 0xf;
  const value = (hash.readUInt32BE(offset) & 0x7fffffff) % 10 ** realm.otpPolicyDigits;
  return String(value).padStart(realm.otpPolicyDigits, "0");
};
const counterNow = () => Math.floor(Date.now() / 1000 / realm.otpPolicyPeriod);

await admin("/users", {
  method: "POST",
  body: JSON.stringify({
    username: EMAIL,
    email: EMAIL,
    firstName: "Keyboard",
    lastName: "Check",
    enabled: true,
    emailVerified: true,
    requiredActions: ["CONFIGURE_TOTP", "CONFIGURE_RECOVERY_AUTHN_CODES"],
    credentials: [{ type: "password", value: PASSWORD, temporary: false }],
  }),
});
const [user] = await admin(`/users?email=${encodeURIComponent(EMAIL)}&exact=true`);

const browser = await launch({
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
});
const page = await browser.newPage();

// What has focus, as a person hears it: its role or tag, id and accessible name
const focused = () =>
  page.evaluate(() => {
    const e = document.activeElement;
    if (!e || e === document.body) return "(page)";
    const name =
      e.getAttribute("aria-label") ||
      [...(e.labels ?? [])].map((l) => l.textContent.trim()).join(" ") ||
      e.textContent.trim() ||
      e.value ||
      "";
    return `${e.getAttribute("role") || e.tagName.toLowerCase()}${e.id ? "#" + e.id : ""} "${name.replace(/\s+/g, " ").slice(0, 40)}"`;
  });
// Tab until `wanted` (a regex on focused()) has focus; returns the stops on the way
async function tabTo(wanted, max = 25) {
  const stops = [];
  for (let i = 0; i < max; i++) {
    const now = await focused();
    if (wanted.test(now)) return stops;
    stops.push(now);
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab never reached ${wanted} (${stops.join(" → ")})`);
}
// Enter on what has focus, and the page it leads to
const enter = () => Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 20_000 }), page.keyboard.press("Enter")]);
// Every stop of the page's Tab order, from the top, until it comes round
async function tabOrder() {
  await page.evaluate(() => document.activeElement?.blur());
  const stops = [];
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const now = await focused();
    if (stops.includes(now)) break;
    stops.push(now);
  }
  return stops;
}
const inApp = () => page.url().startsWith(`${APP}/`) && !page.url().includes("/signed-out");
const signOut = async () => {
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0" }),
    page.evaluate(() => document.querySelector('form[action="/auth/logout"]').requestSubmit()),
  ]);
};
// Email and password, by keyboard, from a fresh sign-in page
async function password() {
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  const start = await focused();
  await tabTo(/#username/);
  await page.keyboard.type(EMAIL);
  await tabTo(/#password/);
  await page.keyboard.type(PASSWORD);
  await enter();
  return start;
}

// WCAG 2.2, 3.3.8 Accessible Authentication: a password or a code needn't be remembered or
// transcribed if it can be filled or pasted. Each field's autocomplete (what a password manager
// reads), and whether a paste into it goes through (no handler cancels it) (P3-D10)
async function fillable(...selectors) {
  return page.evaluate(
    (selectors) =>
      selectors.map((selector) => {
        const field = document.querySelector(selector);
        if (!field) return { selector, missing: true };
        const data = new DataTransfer();
        data.setData("text/plain", "123456");
        const pasted = field.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
        return { selector, autocomplete: field.getAttribute("autocomplete") ?? "(none)", pasted };
      }),
    selectors,
  );
}
const described = (fields) => fields.map((f) => (f.missing ? `${f.selector} missing` : `${f.selector} ${f.autocomplete}${f.pasted ? "" : ", paste refused"}`)).join("; ");
const passwordsFill = (fields) => fields.every((f) => !f.missing && f.pasted && f.autocomplete !== "off" && f.autocomplete !== "(none)");
const codesPaste = (fields) => fields.every((f) => !f.missing && f.pasted);

try {
  // 2. Sign in
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  console.log("      sign-in Tab order:", (await tabOrder()).join(" → "));
  const signInFields = await fillable("#username", "#password");
  check(passwordsFill(signInFields), "sign-in: a password manager can fill Email and Password, and both take a paste", described(signInFields));
  const start = await password();
  check(/#username/.test(start), "sign-in: focus starts in Email", start);

  // 3. The authenticator app
  const secret = await page.$eval("#totpSecret", (input) => input.value).catch(() => null);
  if (!check(!!secret, "after the password, Keycloak asks to set up an authenticator app", page.url())) throw new Error("cannot continue");
  console.log("      authenticator set-up Tab order:", (await tabOrder()).join(" → "));
  const setUpFields = await fillable("#totp");
  check(codesPaste(setUpFields), "the set-up's code field takes a paste", described(setUpFields));
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Tab");
  await tabTo(/#totp\b/);
  const setUpWith = counterNow();
  await page.keyboard.type(totp(secret, setUpWith));
  await enter();

  // 4. The recovery codes
  // Each item is its number, then the code
  const codes = await page.$$eval("#kc-recovery-codes-list li > span:last-child", (spans) => spans.map((span) => span.textContent.trim())).catch(() => []);
  if (!check(codes.length > 0, "then it shows recovery codes to save", `${codes.length} codes`)) throw new Error("cannot continue");
  console.log("      recovery codes Tab order:", (await tabOrder()).join(" → "));
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Tab");
  await tabTo(/#kcRecoveryCodesConfirmationCheck/);
  await page.keyboard.press(" "); // " ", not "Space": Firefox's keyboard (WebDriver BiDi) knows no key by that name
  const ticked = await page.$eval("#kcRecoveryCodesConfirmationCheck", (c) => c.checked);
  await tabTo(/#saveRecoveryAuthnCodesBtn/);
  await enter();
  check(ticked && inApp(), "the codes' checkbox by Tab and Space, Enter saves them: signed in", page.url());
  const kinds = (await admin(`/users/${user.id}/credentials`)).map((c) => c.type).sort();
  check(kinds.includes("otp") && kinds.includes("recovery-authn-codes"), "Keycloak holds the authenticator app and the recovery codes", kinds.join(", "));

  // 5. The authenticator code
  await signOut();
  await password();
  const atCode = await focused();
  check(/#otp\b/.test(atCode), "after the password, focus lands in the authenticator code", atCode);
  console.log("      authenticator code Tab order:", (await tabOrder()).join(" → "));
  const codeFields = await fillable("#otp");
  check(codesPaste(codeFields), "the authenticator code field takes a paste", described(codeFields));
  await page.evaluate(() => document.querySelector("#otp").focus());
  await page.keyboard.type("000000");
  await enter();
  const refused = await page.evaluate(() => {
    const field = document.querySelector("#otp");
    const said = (field?.getAttribute("aria-describedby") ?? "")
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent.trim())
      .filter(Boolean)
      .join(" ");
    return { invalid: field?.getAttribute("aria-invalid"), said, focus: document.activeElement === field };
  });
  check(refused.invalid === "true" && refused.said.length > 0, "a wrong code is refused at the field, its message named by the field", `${refused.said}; focus in the field: ${refused.focus}`);
  if (!refused.focus) await page.evaluate(() => document.querySelector("#otp").focus());
  // Keycloak takes each code once: the set-up's, still current, would be refused
  while (counterNow() === setUpWith) await new Promise((resolve) => setTimeout(resolve, 1000));
  await page.keyboard.type(totp(secret, counterNow()));
  await enter();
  check(inApp(), "the right code, by keyboard: signed in", page.url());

  // 6. A recovery code
  await signOut();
  await password();
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Tab");
  const toAnother = await tabTo(/Try another way/i);
  await enter();
  console.log("      “Try another way” Tab order:", (await tabOrder()).join(" → "));
  // What a screen reader says for each way: Chrome's accessible names
  const buttons = (node) => [...(node?.role === "button" ? [node.name] : []), ...(node?.children ?? []).flatMap(buttons)];
  const ways = buttons(await page.accessibility.snapshot({ interestingOnly: true })).filter((name) => !/different account/i.test(name));
  check(ways.length >= 2 && ways.every((name) => !/[a-z][A-Z]/.test(name)), "each way to sign in is named in words a screen reader can say", ways.join(" | "));
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Tab");
  await tabTo(/recovery/i);
  const chooser = await focused();
  if (!/#recoveryCodeInput/.test(chooser)) await enter(); // the choice of method, then its page
  const atRecovery = await focused();
  if (!/#recoveryCodeInput/.test(atRecovery)) await tabTo(/#recoveryCodeInput/);
  const recoveryFields = await fillable("#recoveryCodeInput");
  check(codesPaste(recoveryFields), "the recovery code field takes a paste", described(recoveryFields));
  await page.keyboard.type(codes[0]);
  await enter();
  if (!inApp()) {
    const said = await page.evaluate(() => [...document.querySelectorAll("[role=alert], .kc-feedback-text, [id^=input-error]")].map((e) => e.textContent.trim()).join(" | "));
    console.log(`      still on ${new URL(page.url()).pathname}: “${said}”; the code typed was ${codes[0].replace(/[A-Za-z0-9]/g, "x")}`);
  }
  check(inApp(), "“Try another way” and a recovery code, by keyboard: signed in", `${toAnother.length} Tab stops to it; ${page.url()}`);
} catch (error) {
  check(false, "the keyboard walk", error.message);
} finally {
  await browser.close();
  await admin(`/users/${user.id}`, { method: "DELETE" }).catch((e) => console.log(`(the user wasn't deleted: ${e.message})`));
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall keyboard checks passed");
process.exit(failures ? 1 : 0);
