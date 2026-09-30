// Checks the guide page itself, no stacks needed: at phone, laptop and desktop widths, light and
// dark, no script errors, no sideways scrolling, and no serious or critical WCAG 2.2 A/AA
// violations (axe-core). Screenshots go to out/page-*.png.
//   node page.mjs
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import puppeteer from "puppeteer-core";
import { CHROME, ROOT, check, failed } from "./lib.mjs";

const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const url = `file://${ROOT}gen9-learn/index.html`;
const out = new URL("./out/", import.meta.url).pathname;
mkdirSync(out, { recursive: true });

const views = [
  ["desktop", 1440, 900],
  ["laptop", 1100, 800],
  ["phone", 390, 844],
];
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true });
try {
  for (const [name, width, height] of views) {
    for (const scheme of ["light", "dark"]) {
      const page = await browser.newPage();
      await page.setViewport({ width, height, isMobile: width < 500, hasTouch: width < 500 });
      await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      await page.goto(url, { waitUntil: "load" });
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => document.querySelector("#b1-4").scrollIntoView());
      await new Promise((resolve) => setTimeout(resolve, 500));
      await page.screenshot({ path: `${out}page-${name}-${scheme}.png` });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      await page.addScriptTag({ content: AXE });
      const result = await page.evaluate(() =>
        window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }),
      );
      const blocking = result.violations.filter((v) => ["serious", "critical"].includes(v.impact));
      check(errors.length === 0, `${name} ${scheme}: no script errors`, errors.join(" | "));
      check(overflow <= 0, `${name} ${scheme}: no sideways scrolling`, overflow > 0 ? `${overflow}px` : "");
      check(blocking.length === 0, `${name} ${scheme}: no serious or critical axe violations`,
        blocking.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target}`).join(" | "));
      for (const v of result.violations.filter((v) => !blocking.includes(v))) console.log(`      ${v.impact}: ${v.id} (${v.nodes.length})`);
      await page.close();
    }
  }
} finally {
  await browser.close();
}
console.log(failed() ? `\n${failed()} check(s) failed` : "\nall checks passed");
process.exit(failed() ? 1 : 0);
