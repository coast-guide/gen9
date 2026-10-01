import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { AuditEvent } from "@/lib/agent";
import { whatHappened, whoActed } from "@/lib/audit-words";

const event = (over: Partial<AuditEvent>): AuditEvent => ({
  id: 1,
  at: "2026-09-26T00:00:00Z",
  actor: "sub-ada",
  actor_email: "ada@gen9.test",
  action: "admin.user.update",
  outcome: "success",
  target: "sub-mary",
  target_email: "mary@gen9.test",
  where: "PATCH /v1/admin/users/{user_id}",
  detail: {},
  ...over,
});

describe("audit words", () => {
  it("says what an admin did to whom, never an action code", () => {
    expect(whatHappened(event({ detail: { admin: true } }))).toBe("Made mary@gen9.test an admin");
    expect(whatHappened(event({ detail: { admin: true, signed_out: true } }))).toBe(
      "Made mary@gen9.test an admin, and signed them out: admins need a second step, and they had none",
    );
    expect(whatHappened(event({ detail: { admin: false } }))).toBe("Removed admin access from mary@gen9.test");
    expect(whatHappened(event({ detail: { enabled: false } }))).toBe("Disabled mary@gen9.test");
    expect(whatHappened(event({ action: "admin.user.delete", target_email: null }))).toBe("Deleted a person");
    expect(whatHappened(event({ action: "admin.plugin.availability", detail: { plugin: "tracker", availability: "installed" } }))).toBe(
      "Set who may have tracker: everyone",
    );
  });

  it("names the tools a person let Gen9 use after they changed", () => {
    expect(whatHappened(event({ action: "connector.tools.keep", detail: { name: "words", tools: ["define", "lookup"] } }))).toBe(
      "Let Gen9 use the changed tools of the connector words (define, lookup)",
    );
  });

  it("names a secret and its host, and a refusal by its reason", () => {
    expect(whatHappened(event({ action: "environment_secret.add", detail: { name: "gh", host: "api.github.com" } }))).toBe(
      "Added the environment secret gh for api.github.com",
    );
    expect(whatHappened(event({ action: "environment_secret.add", detail: { name: "gh", host: "api.github.com", methods: "read" } }))).toBe(
      "Added the environment secret gh for api.github.com, for reading only",
    );
    expect(whatHappened(event({ action: "environment_secret.add", detail: { name: "gh", host: "api.github.com", methods: "all" } }))).toBe(
      "Added the environment secret gh for api.github.com, for reading and changing",
    );
    expect(whatHappened(event({ action: "thread.access", outcome: "denied" }))).toBe("Tried to open someone else’s chat");
    expect(whatHappened(event({ action: "run.access", outcome: "denied" }))).toBe("Tried to reach someone else’s answer");
    expect(whatHappened(event({ action: "file.access", outcome: "denied" }))).toBe("Tried to open someone else’s file");
    expect(whatHappened(event({ action: "access.refused", detail: { reason: "Requires role gen9-admin" } }))).toBe(
      "Was refused something only admins may do",
    );
    expect(whatHappened(event({ action: "access.refused", detail: { reason: "This task's person can't use Gen9 right now." } }))).toBe(
      "Was refused: This task's person can't use Gen9 right now.",
    );
  });

  it("says who, even when Gen9 no longer knows them", () => {
    expect(whoActed(event({}))).toBe("ada@gen9.test");
    expect(whoActed(event({ actor_email: null }))).toBe("A person Gen9 no longer knows");
    expect(whoActed(event({ actor: "unknown", actor_email: null }))).toBe("Someone Gen9 couldn’t identify");
    // Gen9's own actors aren't people it forgot
    expect(whoActed(event({ actor: "sweep", actor_email: null }))).toBe("Gen9");
    expect(whoActed(event({ actor: "gen9-agent-stop", actor_email: null }))).toBe("The operator");
  });

  it("says what Gen9 and the operator did", () => {
    expect(whatHappened(event({ action: "operator.stop", detail: { runs: 2, schedules: 3 } }))).toBe("Stopped every agent: 2 answers stopped, 3 scheduled tasks paused");
    expect(whatHappened(event({ action: "account.sweep", target_email: null }))).toBe("Removed what Gen9 kept of a person, deleted in Keycloak");
    expect(whatHappened(event({ action: "thread.delete" }))).toBe("Deleted a chat");
  });

  // Every action gen9-agent records, read from its code, has words: six once reached the screen as
  // codes (docs/plans/gen9-learn.md, M9, F9)
  it("has words for every action gen9-agent records", () => {
    const root = fileURLToPath(new URL("../../gen9-agent/src/gen9_agent/", import.meta.url));
    const files = (readdirSync(root, { recursive: true }) as string[]).filter((f) => f.endsWith(".py"));
    const actions = new Set<string>();
    for (const file of files) {
      const code = readFileSync(join(root, file), "utf8");
      // An action passed to record(…), _record(…) or AuditEvent(…): a dotted code in the call
      for (const call of code.matchAll(/(?:\brecord|\b_record|AuditEvent)\(([\s\S]{0,400}?)\)\n/g)) {
        for (const [, code_] of call[1].matchAll(/"([a-z_]+(?:\.[a-z_]+)+)"/g)) actions.add(code_);
      }
      // A refused visit to another person's thing: theirs(…, "<kind>") records "<kind>.access". The
      // kind is the last argument, after any query in parentheses
      for (const call of code.matchAll(/theirs(?:_through)?\(/g)) {
        const kind = code.slice(call.index, call.index + 600).match(/"([a-z_]+)",?\s*\)/);
        if (kind) actions.add(`${kind[1]}.access`);
      }
    }
    expect(actions.size).toBeGreaterThan(25);
    const unworded = [...actions].filter((action) => whatHappened(event({ action })) === action);
    expect(unworded).toEqual([]);
  });
});
