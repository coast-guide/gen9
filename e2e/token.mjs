// Sign a seeded user in from the terminal client, so a script can call gen9-agent with a real token:
//
//   GEN9_CONFIG_DIR=/some/private/dir node e2e/token.mjs [admin|user]
//
// Runs `gen9 login` (OAuth device grant, gen9-cli) and confirms its code in headless Chrome with the
// seeded user's password from gen9-keycloak/.env, as a person would (signin.mjs). The tokens land in
// $GEN9_CONFIG_DIR/credentials.json (mode 600, written by gen9-cli), which `gen9` refreshes on each
// command; read `access_token` from there. Prints only "Signed in as …", never a token.
import { existsSync, readFileSync } from "node:fs";
import { ROOT, signInTerminal } from "./signin.mjs";

const env = Object.fromEntries(
  (existsSync(`${ROOT}gen9-keycloak/.env`) ? readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8") : "")
    .split("\n")
    .filter((line) => /^[A-Z0-9_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const who = process.argv[2] === "admin" ? "ADMIN" : "USER";
const EMAIL = env[`GEN9_SEED_${who}_EMAIL`];
const PASSWORD = env[`GEN9_SEED_${who}_PASSWORD`];
if (!process.env.GEN9_CONFIG_DIR) throw new Error("Set GEN9_CONFIG_DIR to a private directory");
if (!EMAIL || !PASSWORD) throw new Error(`No GEN9_SEED_${who}_EMAIL/PASSWORD in gen9-keycloak/.env`);

const line = await signInTerminal({ email: EMAIL, password: PASSWORD, configDir: process.env.GEN9_CONFIG_DIR });
console.log(line ?? "gen9 login did not finish");
process.exit(line ? 0 : 1);
