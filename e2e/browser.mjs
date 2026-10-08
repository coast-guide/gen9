// The browser the checks drive: Chrome by default; Firefox with GEN9_BROWSER=firefox and
// FIREFOX_PATH (e2e/README.md, "Other browsers"). Firefox goes through WebDriver BiDi, so a part
// of a check that talks to Chrome's DevTools protocol (a CDP session) can't run there and says so.
import puppeteer from "puppeteer-core";

export const BROWSER = process.env.GEN9_BROWSER ?? "chrome";
export const FIREFOX = BROWSER === "firefox";
// Chrome where each platform installs it; CHROME_PATH for anywhere else (Chromium, a beta channel)
export const CHROME =
  process.env.CHROME_PATH ?? (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "/usr/bin/google-chrome");

// E2E_INSECURE_CERTS=1: Gen9 under a domain whose certificates come from gen9-edge's own CA,
// which this machine's browser doesn't trust (gen9-edge/README.md); only the certificate's check
// goes: what is served, redirects and cookies are the same
export const INSECURE = process.env.E2E_INSECURE_CERTS === "1" ? { acceptInsecureCerts: true } : {};

/** puppeteer.launch with Chrome's options, carried over to Firefox when it's the one asked for. */
export async function launch(options = {}) {
  options = { ...INSECURE, ...options };
  if (!FIREFOX) return puppeteer.launch({ executablePath: CHROME, ...options });
  if (!process.env.FIREFOX_PATH) {
    throw new Error("GEN9_BROWSER=firefox needs FIREFOX_PATH: npx @puppeteer/browsers install firefox@stable prints it");
  }
  const { args = [], executablePath: _chrome, extraPrefsFirefox = {}, ...rest } = options;
  const prefs = { ...extraPrefsFirefox };
  for (const arg of args) {
    // Chrome's --host-resolver-rules="MAP host 127.0.0.1": Firefox resolves these to localhost
    const map = arg.match(/^--host-resolver-rules=MAP (\S+) 127\.0\.0\.1$/);
    if (map) prefs["network.dns.localDomains"] = map[1];
    else throw new Error(`no Firefox equivalent for ${arg}`);
  }
  return puppeteer.launch({ ...rest, browser: "firefox", executablePath: process.env.FIREFOX_PATH, extraPrefsFirefox: prefs });
}

export const NO_CDP = "it reads Chrome's DevTools protocol, which Firefox doesn't speak";
// Headless Firefox moves focus past a page's last control into its own toolbar, and keeps it
// there through navigations: no click, focus() or new tab brings it back, only more Tabs, which
// come back at an arbitrary control (P4-B1, probed)
export const TOOLBAR_FOCUS = "its Tab walks go round past the page's end, where headless Firefox keeps focus in its own toolbar";

/** Under Firefox, whether to skip a part that needs Chrome; says so when it does. */
export function chromeOnly(what, why = NO_CDP) {
  if (!FIREFOX) return false;
  console.log(`skip  ${what}: ${why}`);
  return true;
}

/**
 * The page in light or dark on its next load. Chrome emulates the system's preference; Firefox has
 * no such emulation over WebDriver BiDi, so it picks it as a person does in Settings > Appearance
 * (next-themes keeps it in localStorage as `theme`).
 */
export async function colourScheme(page, scheme) {
  if (FIREFOX) await page.evaluate((s) => localStorage.setItem("theme", s), scheme);
  else await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
}

/** Chrome: let an injected script past the page's CSP (Firefox's evaluate needs no bypass). */
export async function bypassCSP(page) {
  if (!FIREFOX) await page.setBypassCSP(true);
}

/** axe-core into the page: a script tag in Chrome (past the CSP), evaluated in Firefox. */
export async function injectAxe(page, source) {
  if (FIREFOX) await page.evaluate(source);
  else await page.addScriptTag({ content: source });
}
