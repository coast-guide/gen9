// Batch 5, a second client: gen9-cli signs in with the OAuth device flow, asks, signs out; then a
// refused consent. The terminal is a public client (no secret): azp=gen9-cli on the same agent API.
import { spawn } from "node:child_process";
import { mkdirSync, rmSync, readFileSync, statSync } from "node:fs";
import { APP, ROOT, check, kcdb, navigation, nextWindow, otpPolicy, settled, sh, totp } from "../lib.mjs";

const events = (sub, since) =>
  kcdb(`select coalesce(string_agg(type || coalesce(':' || client_id, '') || coalesce(':' || error, ''), ' ' order by event_time), '') from event_entity where (user_id = '${sub}' or client_id = 'gen9-cli') and event_time >= ${since}`);

export default async function cli(ctx) {
  const { page, rec, user } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  const config = new URL("../out/cli-config", import.meta.url).pathname;
  rmSync(config, { recursive: true, force: true });
  mkdirSync(config, { recursive: true });
  const env = { ...process.env, GEN9_CONFIG_DIR: config };
  const gen9 = (args) => sh(`cd gen9-cli && GEN9_CONFIG_DIR=${config} uv run -q gen9 ${args}`, { timeout: 180_000 });

  // Confirm a device code in the browser: sign in (password + code) if needed, then the consent page
  const confirm = async (url, answer) => {
    await page.goto(url, { waitUntil: "networkidle0" });
    await settled(page);
    for (let step = 0; step < 6; step++) {
      if (await page.$("#password")) {
        await page.type("#username", user.email);
        await page.type("#password", user.password);
        await navigation(page, page.click("#kc-login"));
      } else if (await page.$("#otp")) {
        user.otpCounter = await nextWindow(policy, user.otpCounter);
        await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
        await navigation(page, page.click("#kc-login"));
      } else if (await page.$(`button[name="${answer}"]`)) {
        obs.consentTitle ??= await page.$eval("h1", (h) => h.textContent.trim());
        await navigation(page, page.click(`button[name="${answer}"]`));
      } else break;
    }
  };
  const login = (answer) =>
    new Promise((resolve) => {
      const child = spawn("uv", ["run", "-q", "gen9", "login"], { cwd: `${ROOT}gen9-cli`, env });
      let out = "";
      let confirming = false;
      const onData = (data) => {
        out += data;
        const url = out.match(/http\S+user_code=\S+/)?.[0];
        if (url && !confirming) {
          confirming = true;
          confirm(url, answer).catch((e) => (out += `\n(confirm failed: ${e.message})`));
        }
      };
      child.stdout.on("data", onData);
      child.stderr.on("data", onData);
      child.on("exit", (code) => resolve({ code, out: out.replace(/user_code=\S+/g, "user_code=…").replace(/\b[A-Z]{4}-[A-Z]{4}\b/g, "XXXX-XXXX") }));
    });

  rec.mark("5.1 gen9 login");
  let since = Date.now();
  const signedIn = await login("accept");
  obs.loginOutput = signedIn.out.trim().split("\n").filter(Boolean);
  check(signedIn.code === 0 && /Signed in as/.test(signedIn.out), "gen9 login: code confirmed in the browser, consent given", obs.loginOutput.at(-1));
  obs.loginEvents = events(user.sub, since);
  obs.consent = kcdb(`select c.client_id from user_consent uc join client c on c.id = uc.client_id where uc.user_id = '${user.sub}'`);
  const file = `${config}/credentials.json`;
  obs.credentialsFile = { mode: (statSync(file).mode & 0o777).toString(8), keys: Object.keys(JSON.parse(readFileSync(file, "utf8"))) };
  const tokens = JSON.parse(readFileSync(file, "utf8"));
  const claims = JSON.parse(Buffer.from(tokens.access_token.split(".")[1], "base64url").toString());
  obs.cliTokenClaims = { azp: claims.azp, aud: claims.aud, typ: claims.typ, scope: claims.scope, lifetime_s: claims.exp - claims.iat };
  check(obs.credentialsFile.mode === "600" && claims.azp === "gen9-cli", "tokens in credentials.json, mode 600; the access token names azp=gen9-cli", JSON.stringify(obs.cliTokenClaims));
  // The web app's Settings, Apps with access: the consent, in its consent screen's words
  const appsWithAccess = async () => {
    await page.goto(`${APP}/settings`, { waitUntil: "networkidle0" });
    return page.evaluate(() => ([...document.querySelectorAll("section")].find((s) => s.querySelector("h2")?.textContent === "Apps with access")?.innerText ?? "").replace(/\s+/g, " "));
  };
  obs.appsAfterLogin = await appsWithAccess();
  check(
    /Gen9 CLI Can see your name; see your email address; see your role in Gen9 \(member or admin\)\. Allowed/.test(obs.appsAfterLogin) && obs.appsAfterLogin.includes("Remove access"),
    "Settings, Apps with access: Gen9 CLI, with what it may do",
    obs.appsAfterLogin.slice(0, 200),
  );

  rec.mark("5.2 whoami and ask");
  obs.whoami = gen9("whoami").out;
  const asked = gen9(`ask "Reply with one word: pong"`);
  obs.ask = asked.out.split("\n").slice(0, 3);
  check(asked.code === 0 && /pong/i.test(asked.out), "gen9 ask streams an answer from the same agent API", obs.ask[0]);
  obs.agentLog = sh(`docker logs --since 2m gen9-agent-api-1 2>&1 | grep -E '"(POST|GET) /v1/(me|threads)' | tail -3`).out.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, "…");

  rec.mark("5.3 gen9 logout");
  since = Date.now();
  obs.logout = gen9("logout").out;
  obs.logoutEvents = events(user.sub, since);
  check(/Signed out/.test(obs.logout) && /REVOKE_GRANT/.test(obs.logoutEvents), "gen9 logout revokes the terminal's grant", obs.logoutEvents);
  obs.appsAfterLogout = await appsWithAccess();
  // Revoking its tokens (RFC 7009) leaves Keycloak's consent: Settings still lists it until Remove access
  check(obs.appsAfterLogout.includes("Gen9 CLI") && obs.appsAfterLogout.includes("Remove access"), "…and its consent stays: Apps with access still lists it, until Remove access", obs.appsAfterLogout.slice(0, 160));

  rec.mark("5.4 refused");
  since = Date.now();
  const refused = await login("cancel");
  obs.refusedOutput = refused.out.trim().split("\n").filter(Boolean).at(-1);
  obs.refusedEvents = events(user.sub, since);
  check(refused.code !== 0 && /rejected_by_user|access_denied/.test(obs.refusedEvents), "Don't allow: the terminal is told access was denied", obs.refusedOutput);
  rmSync(config, { recursive: true, force: true });
  obs.console = rec.consoleMessages.filter((m) => m.step.startsWith("5."));
  return obs;
}
