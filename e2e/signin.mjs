// Sign a user in on the terminal client, as a person would: `gen9 login` (gen9-cli, OAuth device
// grant) prints a code, and headless Chrome confirms it with the user's password and consent. The
// tokens land in $GEN9_CONFIG_DIR/credentials.json (mode 600, written by gen9-cli). Used by
// token.mjs (the seeded users) and search.mjs (a throwaway user). Never prints a token.
import { spawn } from "node:child_process";
import puppeteer from "puppeteer-core";
import { CHROME } from "./browser.mjs";

export const ROOT = new URL("..", import.meta.url).pathname;

// Resolves to gen9 login's "Signed in as …" line, or null when it didn't finish
export async function signInTerminal({ email, password, configDir }) {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true });
  async function confirm(url) {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: "networkidle0" });
    for (let step = 0; step < 6; step++) {
      const next = page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {});
      if (await page.$("#password")) {
        await page.type("#username", email);
        await page.type("#password", password);
        await page.click("#kc-login");
      } else if (await page.$('button[name="accept"]')) {
        await page.click('button[name="accept"]');
      } else break;
      await next;
    }
  }
  try {
    return await new Promise((resolve) => {
      const child = spawn("uv", ["run", "-q", "gen9", "login"], {
        cwd: `${ROOT}gen9-cli`,
        env: { ...process.env, GEN9_CONFIG_DIR: configDir },
      });
      let out = "";
      let confirming = false;
      // Once gen9 login has its token the browser closes, which can cut the confirming page short:
      // its error only matters when the sign-in didn't finish
      let failed = null;
      const onData = (data) => {
        out += data;
        const url = out.match(/http\S+user_code=\S+/)?.[0];
        if (url && !confirming) {
          confirming = true;
          confirm(url).catch((e) => (failed = e));
        }
      };
      child.stdout.on("data", onData);
      child.stderr.on("data", onData);
      child.on("exit", () => {
        const signedIn = out.split("\n").find((l) => l.startsWith("Signed in as")) ?? null;
        if (!signedIn && failed) console.error(`confirming the code failed: ${failed.message}`);
        resolve(signedIn);
      });
    });
  } finally {
    await browser.close();
  }
}
