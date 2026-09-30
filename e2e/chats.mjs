// Deleting the chats a check made, as the person who made them: gen9-agent's API with the terminal's
// token (`gen9 whoami` refreshes it first). A chat asked through `gen9 ask` is known by the
// "--thread <id>" line it prints (`chatOf`). Deleting a chat also stops its run and erases its
// traces and environment. Used by the checks that sign the seeded users in, so their account doesn't
// fill up with test chats: those crowd its sidebar and every search of its past chats.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./signin.mjs";

const API = process.env.GEN9_API ?? "http://localhost:17000";

export const chatOf = (out) => out.match(/--thread ([0-9a-f-]{36})/)?.[1];

const whoami = (configDir) =>
  new Promise((resolve) => {
    const child = spawn("uv", ["run", "-q", "gen9", "whoami"], { cwd: `${ROOT}gen9-cli`, env: { ...process.env, GEN9_CONFIG_DIR: configDir }, stdio: "ignore" });
    child.on("exit", resolve);
  });

// Resolves to the ids it couldn't delete (none when all went; 404 counts as gone)
export async function deleteChats(configDir, ids) {
  const wanted = [...new Set(ids.filter(Boolean))];
  if (!wanted.length) return [];
  await whoami(configDir);
  const token = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf8")).access_token;
  const left = [];
  for (const id of wanted) {
    const response = await fetch(`${API}/v1/threads/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
    if (!response || !(response.ok || response.status === 404)) left.push(id);
  }
  return left;
}
