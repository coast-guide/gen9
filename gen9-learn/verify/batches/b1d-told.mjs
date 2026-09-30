// Batch 1, deeper: what a person is told before and as they start. In a fresh browser context (no
// session): the sign-up page's line about their data and its link, and the privacy page it leads
// to (GDPR Art. 13), readable without an account. Then, signed in: the line under the composer that
// says Gen9 is an AI system (the EU AI Act, Art. 50) and the privacy row in Settings. No model call.
import { APP, check } from "../lib.mjs";

export default async function told(ctx) {
  const { browser, page } = ctx;
  const obs = {};

  // A visitor: no session, so the sign-up page itself
  const visitor = await browser.createBrowserContext();
  try {
    const fresh = await visitor.newPage();
    await fresh.goto(`${APP}/auth/login?intent=signup`, { waitUntil: "networkidle0" });
    obs.signupNotice = await fresh.$eval("#gen9-register-privacy", (p) => ({ text: p.textContent.trim(), href: p.querySelector("a")?.getAttribute("href") })).catch(() => null);
    check(
      !!obs.signupNotice && obs.signupNotice.href === `${APP}/privacy`,
      "the sign-up page says how Gen9 uses the person's data, and links to the privacy page",
      obs.signupNotice ? `"${obs.signupNotice.text}" → ${obs.signupNotice.href}` : "no notice",
    );
    // Said once: the card's line replaces the footer link other sign-in pages carry
    obs.signupPrivacyLinks = await fresh.$$eval(`a[href="${APP}/privacy"]`, (as) => as.length);
    check(obs.signupPrivacyLinks === 1, "the sign-up page links to it once, in its card", `${obs.signupPrivacyLinks} link(s)`);
    const response = await fresh.goto(`${APP}/privacy`, { waitUntil: "networkidle0" });
    obs.privacyStatus = response.status();
    obs.privacyUrl = fresh.url();
    obs.privacyHeadings = await fresh.$$eval("main h1, main h2", (hs) => hs.map((h) => h.textContent.trim()));
    obs.privacyController = await fresh.$eval("main", (m) => (/hasn.t named itself/.test(m.textContent) ? "not named yet" : "named"));
    check(
      obs.privacyStatus === 200 && obs.privacyUrl === `${APP}/privacy` && obs.privacyHeadings.length > 2,
      "the privacy page opens without an account",
      `${obs.privacyStatus}; ${obs.privacyHeadings.join(" | ")}; controller ${obs.privacyController}`,
    );
  } finally {
    await visitor.close();
  }

  // Signed in: the AI notice under the composer, before the first question, and Settings' row
  await page.goto(`${APP}/chat`, { waitUntil: "networkidle0" });
  obs.aiNotice = await page.evaluate(() => [...document.querySelectorAll("p")].map((p) => p.textContent.trim()).find((t) => t.startsWith("Gen9 is an AI system")) ?? null);
  check(obs.aiNotice === "Gen9 is an AI system and can be wrong. Check its work before you rely on it.", "under the composer, before the first question: Gen9 is an AI system", obs.aiNotice ?? "none");
  await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
  obs.settingsRow = await page.evaluate(() => {
    const label = [...document.querySelectorAll("p")].find((p) => p.textContent.trim() === "How Gen9 uses your data");
    const row = label?.closest("div")?.parentElement;
    return row ? { text: row.textContent.trim().slice(0, 160), href: row.querySelector("a")?.getAttribute("href") } : null;
  });
  check(obs.settingsRow?.href === "/privacy", "Settings links to the same page, under Your data", JSON.stringify(obs.settingsRow));
  return obs;
}
