import type { AuditEvent } from "@/lib/agent";

/** Who may have a plugin, as the Plugins screen says it (docs/design/information-architecture.md). */
const AVAILABILITY: Record<string, string> = {
  off: "nobody",
  available: "people who add it",
  installed: "everyone",
};

const text = (value: unknown) => (typeof value === "string" || typeof value === "number" ? String(value) : "");

/** Gen9's own actors, never a person's (gen9-agent's deletion.py, stop.py and erase.py). */
const SYSTEM_ACTORS: Record<string, string> = {
  sweep: "Gen9",
  "gen9-agent-stop": "The operator",
  "gen9-agent-erase": "The operator",
};

/** Who acted: their email, Gen9 or the operator, or what Gen9 can say when it doesn't know them any more. */
export function whoActed(event: AuditEvent): string {
  if (event.actor_email) return event.actor_email;
  if (SYSTEM_ACTORS[event.actor]) return SYSTEM_ACTORS[event.actor];
  return event.actor === "unknown" ? "Someone Gen9 couldn’t identify" : "A person Gen9 no longer knows";
}

/**
 * What happened, in a sentence (docs/design/screens/admin-audit.md): the audit's action codes
 * (gen9-agent's audit.py) never reach the screen.
 */
export function whatHappened(event: AuditEvent): string {
  const them = event.target_email ?? "a person";
  const d = event.detail ?? {};
  switch (event.action) {
    case "admin.user.update": {
      const changes = [
        d.admin === true && `Made ${them} an admin`,
        d.admin === false && `Removed admin access from ${them}`,
        d.enabled === true && `Enabled ${them}`,
        d.enabled === false && `Disabled ${them}`,
      ].filter(Boolean);
      return changes.length ? changes.join(", and ") : `Changed ${them}`;
    }
    case "admin.user.unlock":
      return `Unlocked sign-in for ${them}`;
    case "admin.user.logout":
      return `Signed ${them} out everywhere`;
    case "admin.user.password_reset":
      return `Sent ${them} a password reset`;
    case "admin.user.delete":
      return `Deleted ${them}`;
    case "admin.users.remove_deleted":
      return `Removed what Gen9 kept of people deleted in Keycloak (${text(d.removed) || "0"})`;
    case "admin.search.reindex":
      return "Started making older chats searchable by meaning";
    case "admin.directory.sync":
      return "Updated the connector directory";
    case "admin.plugin_source.add":
      return `Added the plugin source ${text(d.url)}`;
    case "admin.plugin_source.sync":
      return "Fetched a plugin source again";
    case "admin.plugin_source.remove":
      return `Removed a plugin source and its plugins (${text(d.plugins) || "0"})`;
    case "admin.plugin.availability":
      return `Set who may have ${text(d.plugin) || "a plugin"}: ${AVAILABILITY[text(d.availability)] ?? text(d.availability)}`;
    case "account.delete":
      return "Deleted their own account";
    case "account.sweep":
      return `Removed what Gen9 kept of ${them}, deleted in Keycloak`;
    case "thread.delete":
      return "Deleted a chat";
    case "operator.stop":
      return `Stopped every agent: ${text(d.runs) || "0"} answers stopped, ${text(d.schedules) || "0"} scheduled tasks paused`;
    case "operator.resume":
      return `Resumed the scheduled tasks a stop paused (${text(d.schedules) || "0"})`;
    case "restore.account.delete":
      return `Deleted ${them} again after a restore: the backup was made before they were deleted`;
    case "restore.thread.delete":
      return "Deleted a chat again after a restore: the backup was made before it was deleted";
    case "account.export":
      return `Downloaded a copy of their data (${text(d.chats) || "0"} chats, ${text(d.files) || "0"} files)`;
    case "connector.add":
      return `Added the connector ${text(d.name)}${d.host ? ` (${text(d.host)})` : ""}`;
    case "connector.remove":
      return `Removed the connector ${text(d.name)}`;
    case "connector.tools.keep":
      return `Let Gen9 use the changed tools of the connector ${text(d.name)}${Array.isArray(d.tools) && d.tools.length ? ` (${d.tools.map(text).join(", ")})` : ""}`;
    case "environment_secret.add":
      // What it was sent for (P5-C2); older events don't say
      return `Added the environment secret ${text(d.name)} for ${text(d.host)}${d.methods === "all" ? ", for reading and changing" : d.methods === "read" ? ", for reading only" : ""}`;
    case "environment_secret.remove":
      return `Removed the environment secret ${text(d.name)}`;
    case "task.trigger.make":
      return "Made an API trigger for a scheduled task";
    case "task.trigger.revoke":
      return "Revoked a scheduled task’s API trigger";
    case "thread.access":
      return "Tried to open someone else’s chat";
    case "run.access":
      return "Tried to reach someone else’s answer";
    case "file.access":
      return "Tried to open someone else’s file";
    case "task.access":
      return "Tried to use someone else’s scheduled task";
    case "connector.access":
      return "Tried to use someone else’s connector";
    case "environment_secret.access":
      return "Tried to remove someone else’s environment secret";
    case "access.refused":
      return /^Requires role gen9-admin/.test(text(d.reason))
        ? "Was refused something only admins may do"
        : `Was refused: ${text(d.reason) || "no reason given"}`;
    default:
      return event.action;
  }
}
