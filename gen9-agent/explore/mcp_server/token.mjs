// Signs the seeded user in on the terminal (Puppeteer types the password) and prints where the
// CLI's credentials landed, for probe.py
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, signInTerminal } from "../../../e2e/signin.mjs";

const env = Object.fromEntries(readFileSync(`${ROOT}gen9-keycloak/.env`, "utf8").split("\n").filter((l) => /^[A-Z0-9_]+=/.test(l)).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const dir = mkdtempSync(join(tmpdir(), "gen9-mcp-probe-"));
await signInTerminal({ email: env.GEN9_SEED_USER_EMAIL, password: env.GEN9_SEED_USER_PASSWORD, configDir: dir });
console.log(join(dir, "credentials.json"));
