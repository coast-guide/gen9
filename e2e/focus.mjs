// A control that takes keyboard focus is never entirely hidden by what's pinned over the page (WCAG
// 2.2, 2.4.11 Focus Not Obscured (Minimum); W3C technique C43; manual-e2e.md, P3-D10): the phones'
// top bar, a chat's title and its composer. As a throwaway user with a long answer (40 links), in
// Chrome, on a desktop and a phone, Tab and then Shift+Tab through the chat, Settings and Search:
// at each stop, five points of the focused control's box (or its label's, for a visually hidden
// radio) are sampled, and at least one must show it. It then opens the chat again: it rests at the
// end, the last link above the composer. At 400% zoom (320×256 CSS px, WCAG 1.4.10 Reflow) nothing
// is pinned and nothing scrolls sideways.
// It deletes the user at the end, whatever happens; the long answer is its one model call.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromeOnly, launch, TOOLBAR_FOCUS } from "./browser.mjs";
import { ROOT, signInTerminal } from "./signin.mjs";

if (chromeOnly("the focus walk", TOOLBAR_FOCUS)) process.exit(0);

const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const KEYCLOAK = process.env.KEYCLOAK_URL ?? "http://localhost:15000";
const EMAIL = `focus-${Date.now()}@gen9.test`;
const PASSWORD = `e2e-${randomBytes(12).toString("hex")}`;
const VIEWPORTS = [
  { label: "desktop", width: 1280, height: 900 },
  { label: "phone", width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  // 400% zoom of a 1280×1024 screen (WCAG 1.4.10 Reflow): nothing pinned, the page all content
  { label: "400% zoom", width: 320, height: 256 },
];

let failures = 0;
function check(ok, what, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `: ${detail}` : ""}`);
}
async function admin(method, path, body) {
  const token = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({ grant_type: "password", client_id: "admin-cli", username: env.KC_BOOTSTRAP_ADMIN_USERNAME, password: env.KC_BOOTSTRAP_ADMIN_PASSWORD }),
  })
    .then((r) => r.json())
    .then((b) => b.access_token);
  const response = await fetch(`${KEYCLOAK}/admin/realms/gen9${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!response.ok && response.status !== 404) throw new Error(`Keycloak ${method} ${path}: ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
function gen9(configDir, args) {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", ...args], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir } });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("close", (code) => resolve({ code, out }));
  });
}

// Tab (or Shift+Tab) round the page once; the stops whose focused control no sampled point shows
async function hiddenStops(page, backwards) {
  const seen = new Set();
  const hidden = [];
  let stops = 0;
  await page.evaluate(() => document.activeElement?.blur());
  for (let i = 0; i < 250; i++) {
    if (backwards) await page.keyboard.down("Shift");
    await page.keyboard.press("Tab");
    if (backwards) await page.keyboard.up("Shift");
    const stop = await page.evaluate(() => {
      const focused = document.activeElement;
      if (!focused || focused === document.body) return null;
      const shown = focused.getBoundingClientRect().width <= 1 ? (focused.closest("label") ?? focused) : focused;
      const r = shown.getBoundingClientRect();
      const points = [
        [r.left + r.width / 2, r.top + r.height / 2],
        [r.left + 2, r.top + 2],
        [r.right - 2, r.top + 2],
        [r.left + 2, r.bottom - 2],
        [r.right - 2, r.bottom - 2],
      ];
      const visible = points.some(([x, y]) => {
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
        const top = document.elementFromPoint(x, y);
        return top && (shown.contains(top) || top.contains(shown) || focused.contains(top));
      });
      const name = `${focused.tagName.toLowerCase()} “${(focused.getAttribute("aria-label") || focused.textContent || "").trim().slice(0, 30)}”`;
      return { key: `${name}@${Math.round(r.top + scrollY)}`, name, visible };
    });
    if (!stop) continue;
    if (seen.has(stop.key)) break;
    seen.add(stop.key);
    stops++;
    if (!stop.visible) hidden.push(stop.name);
  }
  return { stops, hidden };
}

const dir = mkdtempSync(join(tmpdir(), "gen9-focus-"));
const browser = await launch({ headless: !process.env.HEADED });
let id;
try {
  await admin("POST", "/users", { username: EMAIL, email: EMAIL, firstName: "Focus", lastName: "Check", enabled: true, emailVerified: true, credentials: [{ type: "password", value: PASSWORD, temporary: false }] });
  id = (await admin("GET", `/users?email=${encodeURIComponent(EMAIL)}&exact=true`))[0]?.id;
  check(Boolean(await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: dir })), "a throwaway user signs in on the terminal");
  const asked = await gen9(dir, ["ask", "Without searching, give a numbered list of 40 large world cities, each only as a markdown link to its English Wikipedia article. Nothing else."]);
  const thread = asked.out.match(/--thread (\S+)/)?.[1];
  const links = (asked.out.match(/\]\(https?:/g) ?? []).length;
  check(Boolean(thread) && links >= 20, "a long answer to walk through", `${links} links`);

  const page = await browser.newPage();
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", EMAIL);
  await page.type("#password", PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  for (const { label, ...viewport } of VIEWPORTS) {
    await page.setViewport(viewport);
    for (const path of [`/chat/${thread}`, "/settings", "/search?q=wikipedia&mode=keyword"]) {
      for (const backwards of [false, true]) {
        await page.goto(`${APP}${path}`, { waitUntil: "networkidle0" });
        const { stops, hidden } = await hiddenStops(page, backwards);
        check(stops > 3 && hidden.length === 0, `${path.split("?")[0].replace(thread, "<chat>")}, ${label}, ${backwards ? "Shift+Tab" : "Tab"}: nothing that takes focus is hidden`, `${stops} stops${hidden.length ? `; hidden: ${hidden.slice(0, 4).join(", ")}` : ""}`);
      }
    }
    await page.goto(`${APP}/chat/${thread}`, { waitUntil: "networkidle0" });
    const room = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      const pinned = (parseFloat(root.getPropertyValue("--sticky-top")) || 0) + (parseFloat(root.getPropertyValue("--sticky-bottom")) || 0);
      return { pinned, sideways: document.documentElement.scrollWidth - innerWidth };
    });
    check(room.sideways <= 0 && (label === "400% zoom" ? room.pinned === 0 : room.pinned > 0), `the chat, ${label}: ${label === "400% zoom" ? "nothing pinned over it" : "its bars pinned"}, no sideways scroll`, `${room.pinned} px pinned`);
    const end = await page.evaluate(() => {
      const last = [...document.querySelectorAll(".prose-gen9 a")].at(-1)?.getBoundingClientRect();
      const composer = document.querySelector("#composer")?.closest("form")?.getBoundingClientRect();
      return { atEnd: Math.abs(scrollY + innerHeight - document.documentElement.scrollHeight) <= 2, clear: Boolean(last && composer && last.bottom <= composer.top) };
    });
    check(end.atEnd && end.clear, `the chat opens at its end, the last link above the composer (${label})`);
  }
} catch (e) {
  check(false, "the focus check ran to the end", e.message);
} finally {
  await browser.close();
  rmSync(dir, { recursive: true, force: true });
  // gen9-agent's sweep removes the chat once the user is gone from Keycloak
  if (id) await admin("DELETE", `/users/${id}`).catch(() => {});
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
