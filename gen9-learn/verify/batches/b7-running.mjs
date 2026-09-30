// Batch 7, running it: what `make` does at startup, and how the stacks are wired. Destructive steps
// (wiping gen9-postgres, which deletes every chat, between its backup and its restore) run only
// with DESTRUCTIVE=1.
import { existsSync, mkdtempSync, readFileSync, renameSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ROOT, appdb, check, kcdb, sh } from "../lib.mjs";

export default async function running() {
  const obs = {};

  // 7.1 Keycloak restarts: realm import skipped, configure re-applies, ready gates up; sessions persist
  const sessionsBefore = kcdb(`select count(*) from offline_user_session where offline_flag = '0' and realm_id = (select id from realm where name = 'gen9')`);
  const from = new Date(Date.now() - 1000).toISOString();
  const restart = sh("make down STACKS=keycloak && make up STACKS=keycloak", { timeout: 300_000 });
  obs.keycloakRestart = restart.out.split("\n").filter((l) => /^==|Open:|configure|ready|Keycloak admin/.test(l)).slice(0, 12);
  obs.importLine = sh(`docker logs --since ${from} gen9-keycloak-keycloak-1 2>&1 | grep -iE 'import' | tail -2`).out;
  obs.configureLog = sh("docker logs gen9-keycloak-configure-1 2>&1 | tail -5").out.split("\n");
  obs.sessionsAfter = { before: sessionsBefore, after: kcdb(`select count(*) from offline_user_session where offline_flag = '0' and realm_id = (select id from realm where name = 'gen9')`) };
  check(restart.code === 0 && /already exists|skipp/i.test(obs.importLine), "restart: realm import skipped (the realm exists), configure re-applied", obs.importLine.split("\n")[0].slice(-80));
  check(obs.sessionsAfter.after === obs.sessionsAfter.before, "login sessions survived the restart (Keycloak 26 persists them)", JSON.stringify(obs.sessionsAfter));

  // 7.2 The agent's one-shot migrate job runs before the API starts
  const agent = sh("make down STACKS=agent && make up STACKS=agent", { timeout: 300_000 });
  obs.migrateLog = sh("docker logs gen9-agent-migrate-1 2>&1 | tail -3").out.split("\n");
  obs.migrateOrder = agent.out.split("\n").filter((l) => /migrate|api/.test(l)).slice(-6);
  check(agent.code === 0 && obs.migrateLog.some((l) => /migrations applied/.test(l)), "the migrate job ran before the API", obs.migrateLog.at(-1));

  // 7.3 Networks: who can reach whom
  obs.networks = Object.fromEntries(
    ["postgres", "keycloak", "langfuse", "temporal", "models", "agent", "ui"].map((s) => [
      `gen9-${s}`,
      sh(`docker network inspect gen9-${s} -f '{{range .Containers}}{{.Name}} {{end}}'`).out.split(" ").filter(Boolean).sort(),
    ]),
  );
  check(!obs.networks["gen9-postgres"].some((c) => c.startsWith("gen9-ui")), "gen9-ui isn't on gen9-postgres: it can't reach the database", obs.networks["gen9-postgres"].join(" "));
  check(
    obs.networks["gen9-temporal"].includes("gen9-agent-worker-1") && !obs.networks["gen9-temporal"].some((c) => c.startsWith("gen9-ui")),
    "gen9-agent (API and worker) reaches Temporal over gen9-temporal; gen9-ui can't",
    obs.networks["gen9-temporal"].join(" "),
  );
  check(
    obs.networks["gen9-models"].filter((c) => !c.startsWith("gen9-models-")).join(" ") === "gen9-agent-api-1 gen9-agent-worker-1",
    "only gen9-agent reaches the model router over gen9-models: its worker, and its API for search queries",
    obs.networks["gen9-models"].join(" "),
  );
  // The API's router key (models-api.local.env) embeds and reranks, nothing else; prints status codes only
  obs.apiKeyScope = sh(`docker exec -i gen9-agent-api-1 python - <<'PY'
import asyncio, os, httpx
async def main():
    headers = {"Authorization": f"Bearer {os.environ['GEN9_MODELS_KEY']}"}
    async with httpx.AsyncClient(headers=headers, timeout=60) as http:
        calls = [
            ("http://gen9-models:4000/v1/embeddings", {"model": "embed", "input": ["hi"]}),
            ("http://gen9-models:4000/v1/chat/completions", {"model": "chat", "messages": [{"role": "user", "content": "hi"}]}),
            ("http://gen9-models:4000/v1/search/web", {"query": "hi"}),
            ("http://gen9-models-admin:4001/users/nobody/erase", {}),
        ]
        print(*[(await http.post(url, json=body)).status_code for url, body in calls])
asyncio.run(main())
PY`).out;
  check(obs.apiKeyScope === "200 403 403 401", "the API's router key embeds (and reranks) and nothing else: chat 403, web search 403, admin API 401", obs.apiKeyScope);
  obs.config = sh("make config").out.split("\n").slice(-2);

  // 7.3b What each container holds: every gen9-agent service gets the secrets it uses and no
  // others. Names and states only (set, empty, unset), read from Docker; values never leave awk
  obs.holds = sh(`for c in migrate api worker; do docker inspect gen9-agent-$c-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | awk -v c=$c 'BEGIN { n = split("DATABASE_PASSWORD KEYCLOAK_ADMIN_CLIENT_SECRET GEN9_SECRET_KEYS TEMPORAL_PAYLOAD_KEYS GEN9_MODELS_KEY SANDBOX_API_KEY LANGFUSE_SECRET_KEY SMTP_URL", k, " ") } { i = index($0, "="); s[substr($0, 1, i - 1)] = (length($0) > i ? "set" : "empty") } END { printf "%s:", c; for (j = 1; j <= n; j++) printf "%s %s %s", (j > 1 ? "," : ""), k[j], (k[j] in s ? s[k[j]] : "unset"); print "" }'; done`).out;
  const holds = Object.fromEntries(obs.holds.split("\n").map((l) => [l.split(":")[0], Object.fromEntries([...l.matchAll(/(\w+) (set|empty|unset)/g)].map((m) => [m[1], m[2]]))]));
  const only = (c, names) => Object.entries(holds[c] ?? {}).every(([k, v]) => (v === "set") === names.includes(k));
  // Who each connects to Postgres as: the migrate job as the owner, the services as a role that
  // owns nothing (user names, not secrets)
  obs.dbUsers = sh(`for c in migrate api worker; do printf '%s: ' $c; docker inspect gen9-agent-$c-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^DATABASE_USER='; done`).out;
  check(
    obs.dbUsers === "migrate: DATABASE_USER=gen9_agent\napi: DATABASE_USER=gen9_agent_app\nworker: DATABASE_USER=gen9_agent_app",
    "the migrate job connects as the owner (gen9_agent), the API and worker as gen9_agent_app",
    obs.dbUsers.replaceAll("\n", " | "),
  );
  obs.apiInProcess = sh(`docker exec gen9-agent-api-1 python -c "import os; print(*[k + ('=' if os.environ.get(k) else ' empty') for k in ('LANGFUSE_SECRET_KEY', 'SMTP_URL', 'SANDBOX_API_KEY')], os.environ.get('EMAIL_NOTICES'))"`).out;
  check(
    only("migrate", ["DATABASE_PASSWORD"]) &&
      only("api", ["DATABASE_PASSWORD", "KEYCLOAK_ADMIN_CLIENT_SECRET", "GEN9_SECRET_KEYS", "TEMPORAL_PAYLOAD_KEYS", "GEN9_MODELS_KEY"]) &&
      Object.values(holds.worker ?? {}).every((v) => v === "set") &&
      /^LANGFUSE_SECRET_KEY empty SMTP_URL empty SANDBOX_API_KEY empty true$/.test(obs.apiInProcess),
    "each container holds the secrets it uses: migrate the database's, the API no Langfuse, SMTP or sandbox key (notices on, said by EMAIL_NOTICES), the worker all",
    obs.holds.replaceAll("\n", " | "),
  );

  // 7.3c What it may spend: each person's budget, the worker key's daily one, a turn's steps
  const router = (q) => sh(`docker exec gen9-models-postgres-1 psql -U litellm -d litellm -tA -F ' | ' -c "${q.replaceAll('"', '\\"')}"`).out;
  obs.personBudget = router(`select max_budget, budget_duration from "LiteLLM_BudgetTable" where budget_id = 'gen9-user-default'`);
  obs.keyBudgets = router(`select key_alias, coalesce(max_budget::text, 'none'), coalesce(budget_duration, 'none') from "LiteLLM_VerificationToken" where key_alias in ('gen9-agent', 'gen9-agent-api', 'gen9-evals') order by 1`);
  obs.stepBudget = sh(`docker exec gen9-agent-worker-1 python -c "from gen9_agent.grounding import MODEL_CALLS_PER_TURN, MODEL_CALLS_PER_TURN_ALL, SEARCHES_PER_TURN; print(MODEL_CALLS_PER_TURN, MODEL_CALLS_PER_TURN_ALL, SEARCHES_PER_TURN)"`).out;
  check(
    obs.personBudget === "20 | 30d" && /gen9-agent \| 5 \| 1d/.test(obs.keyBudgets) && /gen9-evals \| 5 \| 1d/.test(obs.keyBudgets) && obs.stepBudget === "50 150 12",
    "what it may spend: each person $20 a month (30d, reset on the 1st), the worker's key $5 a day, the evals' key $5 a day; a turn at most 50 model calls an agent, 150 in all, and 12 searches",
    `${obs.personBudget}; ${obs.keyBudgets.replaceAll("\n", " / ")}; steps ${obs.stepBudget}`,
  );

  // 7.3d How good its answers are: the judge of the research evals, against labelled verdicts
  // A fresh install has no verdicts until `make evals` runs (it spends), so the report may say n/a
  obs.calibration = sh("make evals-calibrate REPORT=1", { timeout: 180_000 }).out.split("\n").filter((l) => /verdict\(s\)|agreement|TP /.test(l));
  check(
    obs.calibration.some((l) => /verdict\(s\) scored by people/.test(l)) && obs.calibration.some((l) => /^agreement /.test(l)) &&
      obs.calibration.some((l) => /trusted when TPR and TNR reach 0\.9/.test(l)),
    "the judge's calibration report: people's scores (and any reference labels, apart) against the judge, and when it's trusted",
    obs.calibration.slice(0, 4).join(" | ").slice(0, 200),
  );

  // 7.4 A lost settings file: up refuses and names it, setup rebuilds it from gen9-keycloak/.env
  const file = `${ROOT}gen9-ui/keycloak.local.env`;
  const original = readFileSync(file, "utf8");
  renameSync(file, `${file}.learn-bak`);
  try {
    obs.upRefuses = sh("make up STACKS=ui").out.split("\n").slice(0, 3);
    obs.setupRebuilds = sh("make setup STACKS=keycloak </dev/null").out.split("\n").filter((l) => /wrote|kept|Ready/.test(l));
    check(existsSync(file) && readFileSync(file, "utf8").split("\n").slice(1).join("\n") === original.split("\n").slice(1).join("\n"),
      "make setup rebuilt gen9-ui/keycloak.local.env with the same settings (no new secrets)");
  } finally {
    if (!existsSync(file)) renameSync(`${file}.learn-bak`, file);
    else sh(`rm -f ${file}.learn-bak`);
  }

  // 7.5 Back up gen9-postgres, wipe it (every chat goes), 7.6 restore it (every chat comes back)
  if (process.env.DESTRUCTIVE) {
    const dir = mkdtempSync(join(tmpdir(), "gen9-learn-backup-")); // empty, as make backup wants it
    try {
      obs.chatsBefore = appdb("select count(*) from threads");
      const backup = sh(`make backup STACKS=postgres DIR=${dir}`, { timeout: 300_000 });
      obs.backupOut = backup.out.split("\n").map((l) => l.replace(dir, "<dir>"));
      check(backup.code === 0 && existsSync(`${dir}/manifest`) && /ready/.test(sh("curl -s http://localhost:17000/readyz").out),
        "make backup copied gen9-postgres's volume and settings, and started it again", obs.backupOut.find((l) => /^Backed up/.test(l)));

      const wipe = sh("make wipe STACKS=postgres YES=1", { timeout: 120_000 });
      obs.wipeTail = wipe.out.split("\n").slice(-3);
      sh("make up STACKS=postgres", { timeout: 180_000 });
      obs.readyzAfterWipe = sh("curl -s http://localhost:17000/readyz").out;
      const started = Date.now();
      const back = sh(wipe.out.match(/make up STACKS="[^"]+"/)?.[0] ?? "make up STACKS=\"postgres agent\"", { timeout: 600_000 });
      obs.upAfterWipe = { code: back.code, seconds: Math.round((Date.now() - started) / 1000), restarted: back.out.match(/^restarting .*$/m)?.[0] ?? null, tail: back.out.split("\n").slice(-4) };
      obs.readyzAfterUp = sh("curl -s http://localhost:17000/readyz").out;
      obs.chatsAfterWipe = appdb("select count(*) from threads");
      check(/not migrated/.test(obs.readyzAfterWipe) && /ready/.test(obs.readyzAfterUp) && back.code === 0 && obs.chatsAfterWipe === "0",
        "wipe named the agent; readyz said 'not migrated' until the printed make up; no chats left",
        `${obs.wipeTail.at(-1).trim()}: exit ${back.code} in ${obs.upAfterWipe.seconds} s`);

      const restore = sh(`make restore DIR=${dir} YES=1`, { timeout: 300_000 });
      obs.restoreOut = restore.out.split("\n").filter((l) => !/^(==|  [A-Z][a-z]+ |Open:|NAMES)/.test(l)).map((l) => l.replace(dir, "<dir>"));
      obs.readyzAfterRestore = sh("curl -s http://localhost:17000/readyz").out;
      obs.chatsAfterRestore = appdb("select count(*) from threads");
      check(restore.code === 0 && /ready/.test(obs.readyzAfterRestore) && obs.chatsAfterRestore === obs.chatsBefore,
        "make restore brought gen9-postgres back as it was backed up",
        `chats ${obs.chatsBefore} → ${obs.chatsAfterWipe} → ${obs.chatsAfterRestore}; ${obs.readyzAfterRestore}`);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  } else {
    obs.wipe = "skipped (DESTRUCTIVE=1 runs it: it backs up gen9-postgres, wipes it and restores it)";
  }
  return obs;
}
