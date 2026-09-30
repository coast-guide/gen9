// Accessibility audit of Gen9's screens in real Chrome with axe-core (WCAG 2.2 A/AA rules), on a
// desktop and a phone viewport, in light and dark: signed out (landing, sign-in, sign-up, reset password, signed out) and signed in
// as the seed admin (chat, search, scheduled, settings, users, plugins). Serious and critical violations fail the run;
// moderate and minor ones are listed.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { bypassCSP, chromeOnly, colourScheme, injectAxe, launch } from "./browser.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const env = Object.fromEntries(
  readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const APP = process.env.APP_URL ?? "http://localhost:14000";
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const VIEWPORTS = [
  { label: "desktop", width: 1280, height: 900 },
  { label: "phone", width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 },
];

const browser = await launch({
  headless: !process.env.HEADED,
  defaultViewport: { width: 1280, height: 900 },
});
const page = await browser.newPage();
// gen9-ui's nonce-based CSP would block the injected axe script
await bypassCSP(page);

let failures = 0;
async function audit(name) {
  for (const { label, ...viewport } of VIEWPORTS) {
    await page.setViewport(viewport);
    for (const scheme of ["light", "dark"]) await auditScheme(`${name}, ${label}`, scheme);
  }
}
async function auditScheme(name, scheme) {
  // Load the page with the setting already on, as a user would: switching a loaded page measures
  // colors mid-transition
  await colourScheme(page, scheme);
  await page.reload({ waitUntil: "networkidle0" });
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
  await injectAxe(page, AXE);
  const { violations } = await page.evaluate((tags) => window.axe.run(document, { runOnly: { type: "tag", values: tags } }), TAGS);
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  failures += blocking.length;
  console.log(`${blocking.length ? "FAIL" : "ok  "}  ${name}, ${scheme}`);
  for (const v of violations) {
    console.log(`        ${v.impact}: ${v.id}: ${v.help}`);
    for (const node of v.nodes.slice(0, 3)) {
      const data = node.any[0]?.data;
      const colors = data?.fgColor ? ` (${data.fgColor} on ${data.bgColor}: ${data.contrastRatio}:1)` : "";
      console.log(`          ${node.target.join(" ")}${colors}`);
    }
  }
}
const visit = async (url, name) => {
  await page.goto(url, { waitUntil: "networkidle0" });
  await audit(name);
};

try {
  await visit(`${APP}/`, "landing");
  await visit(`${APP}/auth/login`, "sign-in (Keycloak)");
  await visit(`${APP}/auth/login?intent=signup`, "sign-up (Keycloak)");
  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click('a[href*="reset-credentials"]')]);
  await audit("reset password (Keycloak)");
  await visit(`${APP}/signed-out`, "signed out");
  await visit(`${APP}/privacy`, "privacy (GDPR Art. 13)");

  await page.goto(`${APP}/auth/login`, { waitUntil: "networkidle0" });
  await page.type("#username", env.GEN9_SEED_ADMIN_EMAIL);
  await page.type("#password", env.GEN9_SEED_ADMIN_PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("#kc-login")]);
  // Reduced motion (gen9-theme.css): the spinner's and the skeleton's own classes stop, and run
  // otherwise, which shows the check can see them move (manual-e2e.md, P3-D11)
  const moves = async (reduce) => {
    await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: reduce ? "reduce" : "no-preference" }]);
    return page.evaluate(async () => {
      const running = [];
      for (const cls of ["animate-spin", "animate-pulse"]) {
        const el = document.createElement("div");
        el.className = `${cls} size-4`;
        document.body.append(el);
        await new Promise((r) => setTimeout(r, 200));
        if (el.getAnimations().some((a) => a.playState === "running")) running.push(cls);
        el.remove();
      }
      return running;
    });
  };
  if (!chromeOnly("reduced motion", "Firefox has no media emulation over WebDriver BiDi")) {
    await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
    const still = await moves(true);
    const moving = await moves(false);
    await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "no-preference" }]);
    failures += still.length || moving.length < 2 ? 1 : 0;
    console.log(`${still.length || moving.length < 2 ? "FAIL" : "ok  "}  reduced motion: spinners and skeletons stop (${still.join(", ") || "none running"}; without it, ${moving.join(", ")})`)
  };
  await visit(`${APP}/chat`, "chat");
  await visit(`${APP}/search`, "search");
  await visit(`${APP}/search?mode=keyword&q=pong`, "search results");
  await visit(`${APP}/scheduled`, "scheduled");
  await visit(`${APP}/settings`, "settings");
  await visit(`${APP}/admin/users`, "users (admin)");
  await visit(`${APP}/admin/plugins`, "plugins (admin)");
  await visit(`${APP}/admin/audit`, "audit log (admin)");
} finally {
  // Sign out, so runs don't leave sessions under "Where you're signed in"
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" }).catch(() => {});
  await page
    .evaluate(() => document.querySelector('form[action="/auth/logout"]')?.requestSubmit())
    .then(() => page.waitForNavigation({ waitUntil: "networkidle0" }))
    .catch(() => {});
  await browser.close();
}

console.log(failures ? `\n${failures} serious or critical violation(s)` : "\nno serious or critical violations");
process.exit(failures ? 1 : 0);
